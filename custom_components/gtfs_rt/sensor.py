import logging
import homeassistant.helpers.config_validation as cv
import requests
import voluptuous as vol

from datetime import datetime, timedelta
from dataclasses import dataclass
from typing import Dict, List, Optional, Any, Tuple
from google.transit import gtfs_realtime_pb2
from homeassistant.components.sensor import PLATFORM_SCHEMA
from homeassistant.const import ATTR_LATITUDE, ATTR_LONGITUDE, CONF_NAME
from homeassistant.helpers.entity import Entity
from homeassistant.util import Throttle

try:
    from .static_gtfs import StaticGTFSProcessor
    from .logger_helper import LoggerHelper
    from .stop_details import StopDetails
except ImportError:
    from static_gtfs import StaticGTFSProcessor
    from logger_helper import LoggerHelper
    from stop_details import StopDetails

_LOGGER = logging.getLogger(__name__)

ATTR_STOP_ID = "Stop ID"
ATTR_ROUTE = "Route"
ATTR_DIRECTION_ID = "Direction ID"
ATTR_DUE_IN = "Due in"
ATTR_DUE_AT = "Due at"
ATTR_ICON = "Icon"
ATTR_REAL_TIME = "Real-time"

CONF_API_KEY = "api_key"
CONF_X_API_KEY = "x_api_key"
CONF_API_KEY_HEADER_NAME = "api_key_header"
CONF_STOP_ID = "stopid"
CONF_ROUTE = "route"
CONF_DIRECTION_ID = "directionid"
CONF_DEPARTURES = "departures"
CONF_TRIP_UPDATE_URL = "trip_update_url"
CONF_VEHICLE_POSITION_URL = "vehicle_position_url"
CONF_ROUTE_DELIMITER = "route_delimiter"
CONF_ICON = "icon"
CONF_SERVICE_TYPE = "service_type"
CONF_NEXT_BUS_LIMIT = "next_bus_limit"
CONF_UPDATE_INTERVAL = "update_interval"
CONF_STATIC_GTFS_URL = "static_gtfs_url"
CONF_ENABLE_STATIC_FALLBACK = "enable_static_fallback"

DEFAULT_SERVICE = "Service"
DEFAULT_ICON = "mdi:bus"
DEFAULT_DIRECTION = "0"
DEFAULT_API_KEY_HEADER_NAME = "Authorization"
DEFAULT_NEXT_BUS_LIMIT = 1
DEFAULT_UPDATE_INTERVAL = 60

TIME_STR_FORMAT = "%H:%M"

PLATFORM_SCHEMA = PLATFORM_SCHEMA.extend(
    {
        vol.Required(CONF_TRIP_UPDATE_URL): cv.string,
        vol.Optional(CONF_API_KEY): cv.string,
        vol.Optional(CONF_X_API_KEY): cv.string,
        vol.Optional(
            CONF_API_KEY_HEADER_NAME,
            default=DEFAULT_API_KEY_HEADER_NAME,  # type: ignore
        ): cv.string,
        vol.Optional(CONF_VEHICLE_POSITION_URL): cv.string,
        vol.Optional(CONF_ROUTE_DELIMITER): cv.string,
        vol.Optional(
            CONF_UPDATE_INTERVAL, default=DEFAULT_UPDATE_INTERVAL
        ): cv.positive_int,
        vol.Optional(CONF_STATIC_GTFS_URL): cv.string,
        vol.Optional(CONF_ENABLE_STATIC_FALLBACK, default=False): cv.boolean,
        vol.Optional(CONF_DEPARTURES): [
            {
                vol.Required(CONF_NAME): cv.string,
                vol.Required(CONF_STOP_ID): cv.string,
                vol.Required(CONF_ROUTE): cv.string,
                vol.Optional(
                    CONF_DIRECTION_ID,
                    default=DEFAULT_DIRECTION,  # type: ignore
                ): cv.string,
                vol.Optional(
                    CONF_ICON, default=DEFAULT_ICON  # type: ignore
                ): cv.string,
                vol.Optional(
                    CONF_SERVICE_TYPE, default=DEFAULT_SERVICE  # type: ignore
                ): cv.string,
                vol.Optional(
                    CONF_NEXT_BUS_LIMIT, default=DEFAULT_NEXT_BUS_LIMIT  # type: ignore
                ): cv.positive_int,
            }
        ],
    }
)


async def async_setup_platform(hass, config, add_entities, discovery_info=None):
    """Get the public transport sensor."""
    data = PublicTransportData(
        config.get(CONF_TRIP_UPDATE_URL),
        config.get(CONF_VEHICLE_POSITION_URL),
        config.get(CONF_ROUTE_DELIMITER),
        config.get(CONF_API_KEY),
        config.get(CONF_X_API_KEY),
        config.get(CONF_API_KEY_HEADER_NAME),
        config.get(CONF_UPDATE_INTERVAL, DEFAULT_UPDATE_INTERVAL),
        config.get(CONF_STATIC_GTFS_URL),
        config.get(CONF_ENABLE_STATIC_FALLBACK, False),
    )

    # Start background loading after initialization
    await data.start_load_static_gtfs_data()

    sensors = SensorFactory.create_sensors_from_config(config, data)
    add_entities(sensors)


def due_in_minutes(timestamp):
    now_local = datetime.now()
    diff = timestamp - now_local
    return int(diff.total_seconds() / 60)


class GTFSFeedError(Exception):
    """Exception raised when GTFS feed cannot be retrieved."""

    pass


class GTFSDataProcessor:
    """Handles GTFS feed data processing and parsing."""

    def __init__(self, route_delimiter: Optional[str] = None):
        self.route_delimiter = route_delimiter

    def process_route_id(self, original_route_id: str) -> str:
        """Process route ID based on delimiter configuration."""
        if self.route_delimiter is None:
            return original_route_id

        route_id_split = original_route_id.split(self.route_delimiter)
        if route_id_split[0] == self.route_delimiter:
            return original_route_id
        else:
            processed_id = route_id_split[0]
            return processed_id

    def extract_stop_time(self, stop) -> int:
        # Use stop arrival time; fall back on departure time if not available
        return stop.arrival.time if stop.arrival.time != 0 else stop.departure.time

    def is_future_departure(self, timestamp: int) -> bool:
        return due_in_minutes(datetime.fromtimestamp(timestamp)) >= 0


class GTFSFeedClient:
    """Handles GTFS feed HTTP requests."""

    def __init__(
        self,
        api_key: Optional[str],
        x_api_key: Optional[str],
        api_key_header: Optional[str],
    ):
        self.headers = self._build_headers(api_key, x_api_key, api_key_header)

    def _build_headers(
        self,
        api_key: Optional[str],
        x_api_key: Optional[str],
        api_key_header: Optional[str],
    ) -> Optional[Dict[str, str]]:
        """Build HTTP headers based on provided API keys."""
        if api_key is not None and api_key_header is not None:
            return {api_key_header: api_key}
        elif x_api_key is not None:
            return {"x-api-key": x_api_key}
        return None

    def fetch_feed_entities(self, url: str, label: str) -> List[Any]:
        """Fetch and parse GTFS feed entities from URL."""
        try:
            feed = gtfs_realtime_pb2.FeedMessage()
            response = requests.get(url, headers=self.headers, timeout=20)

            if response.status_code == 200:
                LoggerHelper.log_debug(
                    [f"Successfully updated {label}", str(response.status_code)]
                )
            else:
                LoggerHelper.log_error(
                    [
                        f"Updating {label} got",
                        str(response.status_code),
                        str(response.content),
                    ],
                    logger=_LOGGER,
                )
                raise GTFSFeedError(f"Failed to fetch {label}: {response.status_code}")

            feed.ParseFromString(response.content)
            return feed.entity

        except requests.RequestException as e:
            LoggerHelper.log_error(
                [f"Network error fetching {label}", str(e)], logger=_LOGGER
            )
            raise GTFSFeedError(f"Network error: {e}")
        except Exception as e:
            LoggerHelper.log_error(
                [f"Error parsing {label} feed", str(e)], logger=_LOGGER
            )
            raise GTFSFeedError(f"Parse error: {e}")


class SensorFactory:
    """Factory class for creating sensors based on configuration."""

    @staticmethod
    def create_sensors_from_config(
        config: Dict[str, Any], data: "PublicTransportData"
    ) -> List["PublicTransportSensor"]:
        """Create list of sensors from departure configuration."""
        sensors = []
        departures = config.get(CONF_DEPARTURES, [])

        for departure in departures:
            sensors.extend(SensorFactory._create_sensors_for_departure(departure, data))

        return sensors

    @staticmethod
    def _create_sensors_for_departure(
        departure: Dict[str, Any], data: "PublicTransportData"
    ) -> List["PublicTransportSensor"]:
        """Create sensors for a single departure configuration."""
        next_bus_limit = departure.get(CONF_NEXT_BUS_LIMIT, DEFAULT_NEXT_BUS_LIMIT)
        base_name = departure.get(CONF_NAME)
        sensors = []

        for bus_index in range(next_bus_limit):
            sensor_name = SensorFactory._generate_sensor_name(
                base_name, bus_index, next_bus_limit
            )

            sensors.append(
                PublicTransportSensor(
                    data=data,
                    stop_id=departure.get(CONF_STOP_ID),
                    route=departure.get(CONF_ROUTE),
                    direction=departure.get(CONF_DIRECTION_ID),
                    icon=departure.get(CONF_ICON),
                    service_type=departure.get(CONF_SERVICE_TYPE),
                    name=sensor_name,
                    bus_index=bus_index,
                    next_bus_limit=next_bus_limit,
                )
            )

        return sensors

    @staticmethod
    def _generate_sensor_name(base_name: str, bus_index: int, total_buses: int) -> str:
        """Generate appropriate sensor name based on bus index."""
        if bus_index == 0:
            return f"{base_name} Next"
        else:
            return f"{base_name} Next {bus_index + 1}"


class PublicTransportSensor(Entity):
    """Implementation of a public transport sensor."""

    def __init__(
        self,
        data: "PublicTransportData",
        stop_id: str,
        route: str,
        direction: str,
        icon: str,
        service_type: str,
        name: str,
        bus_index: int = 0,
        next_bus_limit: int = DEFAULT_NEXT_BUS_LIMIT,
    ):
        """Initialize the sensor."""
        self.data = data
        self._name = name
        self._stop_id = stop_id
        self._route = route
        self._direction = direction
        self._icon = icon
        self._service_type = service_type
        self._bus_index = bus_index

        data.add_route_to_process(route, direction, stop_id)
        data.set_next_bus_limit(next_bus_limit)
        self.update()

    @property
    def name(self) -> str:
        """Return the name of the sensor."""
        return self._name

    @property
    def unique_id(self) -> str:
        """Return a unique ID for this sensor."""
        return (
            f"gtfs_rt_{self._route}_{self._stop_id}_{self._direction}_{self._bus_index}"
        )

    def _get_next_services(self) -> List[StopDetails]:
        """Get the next services for this sensor's route/stop/direction."""
        return (
            self.data.info.get(self._route, {})
            .get(self._direction, {})
            .get(self._stop_id, [])
        )

    def _get_service_at_index(self, index: int) -> Optional[StopDetails]:
        """Get service at specific index, if available."""
        next_services = self._get_next_services()
        return next_services[index] if len(next_services) > index else None

    @property
    def state(self) -> str:
        """Return the state of the sensor (minutes until arrival)."""
        current_service = self._get_service_at_index(self._bus_index)
        if current_service:
            return str(due_in_minutes(current_service.arrival_time))
        return "-"

    @property
    def extra_state_attributes(self) -> Dict[str, Any]:
        """Return the state attributes."""
        current_service = self._get_service_at_index(self._bus_index)

        attrs = self._build_base_attributes()

        if current_service:
            attrs.update(self._build_current_service_attributes(current_service))

        return attrs

    def _build_base_attributes(self) -> Dict[str, Any]:
        """Build base attributes that are always present."""
        current_service = self._get_service_at_index(self._bus_index)
        is_real_time = current_service.is_real_time if current_service else True

        return {
            ATTR_DUE_IN: self.state,
            ATTR_STOP_ID: self._stop_id,
            ATTR_ROUTE: self._route,
            ATTR_DIRECTION_ID: self._direction,
            ATTR_REAL_TIME: is_real_time,
        }

    def _build_current_service_attributes(self, service: StopDetails) -> Dict[str, Any]:
        """Build attributes for the current service."""
        attrs = {ATTR_DUE_AT: service.arrival_time.strftime(TIME_STR_FORMAT)}

        if service.position:
            attrs[ATTR_LATITUDE] = service.position.latitude
            attrs[ATTR_LONGITUDE] = service.position.longitude

        return attrs

    @property
    def unit_of_measurement(self) -> str:
        """Return the unit this state is expressed in."""
        return "min"

    @property
    def icon(self) -> str:
        """Return the icon for the sensor."""
        return self._icon

    @property
    def service_type(self) -> str:
        """Return the service type."""
        return self._service_type

    def update(self) -> None:
        """Get the latest data and update the states."""
        self.data.update()
        self._log_sensor_update()

    def _log_sensor_update(self) -> None:
        """Log sensor update information for debugging."""
        LoggerHelper.log_info(["Sensor Update:"])
        LoggerHelper.log_info(["Name", self._name], 1)
        LoggerHelper.log_info([ATTR_ROUTE, self._route], 1)
        LoggerHelper.log_info([ATTR_STOP_ID, self._stop_id], 1)
        LoggerHelper.log_info([ATTR_DIRECTION_ID, self._direction], 1)
        LoggerHelper.log_info([ATTR_ICON, self._icon], 1)
        LoggerHelper.log_info(["unit_of_measurement", self.unit_of_measurement], 1)
        LoggerHelper.log_info([ATTR_DUE_IN, self.state], 1)

        # Log additional attributes with error handling
        attrs = self.extra_state_attributes
        self._log_attribute_safely(ATTR_DUE_AT, attrs)
        self._log_attribute_safely(ATTR_LATITUDE, attrs)
        self._log_attribute_safely(ATTR_LONGITUDE, attrs)
        self._log_attribute_safely(ATTR_REAL_TIME, attrs)

    def _log_attribute_safely(self, attr_name: str, attrs: Dict[str, Any]) -> None:
        """Log attribute value safely, handling missing keys."""
        value = attrs.get(attr_name, "not defined")
        LoggerHelper.log_info([attr_name, str(value)], 1)


class PublicTransportData:
    """The Class for handling the data retrieval."""

    def __init__(
        self,
        trip_update_url: str,
        vehicle_position_url: str = "",
        route_delimiter: Optional[str] = None,
        api_key: Optional[str] = None,
        x_api_key: Optional[str] = None,
        api_key_header: Optional[str] = None,
        update_interval: int = DEFAULT_UPDATE_INTERVAL,
        static_gtfs_url: Optional[str] = None,
        enable_static_fallback: bool = False,
    ):
        """Initialize the info object."""
        self._trip_update_url = trip_update_url
        self._vehicle_position_url = vehicle_position_url
        self._update_interval = timedelta(seconds=update_interval)
        self._static_gtfs_url = static_gtfs_url
        self._enable_static_fallback = enable_static_fallback
        self._next_bus_limit = DEFAULT_NEXT_BUS_LIMIT
        self._routes_to_process: Dict[str, Dict[str, Dict[str]]] = {}

        # Initialize helper classes
        self._feed_client = GTFSFeedClient(api_key, x_api_key, api_key_header)
        self._data_processor = GTFSDataProcessor(route_delimiter)
        if self._enable_static_fallback and static_gtfs_url:
            self._static_processor = StaticGTFSProcessor(static_gtfs_url)
        else:
            self._static_processor = None

        self.info: Dict[str, Dict[str, Dict[str, List[StopDetails]]]] = {}

        # Apply throttling decorator to the update method
        self.update = Throttle(self._update_interval)(self._update)

        self._log_configuration()

    async def start_load_static_gtfs_data(self) -> None:
        """Start background loading of static GTFS data if available."""

        if self._static_processor:
            await self._static_processor.load_gtfs_data()

    def _update(self) -> None:
        """Update the transit data (internal method with throttling applied)."""

        try:
            vehicle_positions = (
                self._get_vehicle_positions() if self._vehicle_position_url else {}
            )
            self._update_route_statuses(vehicle_positions)
        except GTFSFeedError as e:
            LoggerHelper.log_error(
                [f"Failed to update transit data: {e}"], logger=_LOGGER
            )

    def _log_configuration(self) -> None:
        """Log current configuration for debugging."""
        LoggerHelper.log_debug(["trip_update_url", self._trip_update_url])
        LoggerHelper.log_debug(["vehicle_position_url", self._vehicle_position_url])
        LoggerHelper.log_debug(
            ["route_delimiter", str(self._data_processor.route_delimiter)]
        )
        LoggerHelper.log_debug(["headers", str(self._feed_client.headers)])
        LoggerHelper.log_debug(
            ["update_interval", f"{self._update_interval.total_seconds()}s"]
        )
        LoggerHelper.log_debug(["static_gtfs_url", str(self._static_gtfs_url)])
        LoggerHelper.log_debug(
            ["enable_static_fallback", str(self._enable_static_fallback)]
        )

    def _update_route_statuses(self, vehicle_positions: Dict[str, Any]) -> None:
        """Get the latest trip update data and process it."""
        departure_times: Dict[str, Dict[str, Dict[str, List[StopDetails]]]] = {}

        try:
            feed_entities = self._feed_client.fetch_feed_entities(
                self._trip_update_url, "trip data"
            )

            for entity in feed_entities:
                if entity.HasField("trip_update"):
                    self._process_trip_update(
                        entity, departure_times, vehicle_positions
                    )

            # Apply static fallback if enabled
            if self._enable_static_fallback:
                self._apply_static_fallback(departure_times)

            self._sort_departure_times(departure_times)
            self.info = departure_times

        except GTFSFeedError as e:
            LoggerHelper.log_error(
                [f"Error updating route statuses: {e}"], logger=_LOGGER
            )

    def _process_trip_update(
        self, entity: Any, departure_times: Dict, vehicle_positions: Dict[str, Any]
    ) -> None:
        """Process a single trip update entity."""
        trip = entity.trip_update.trip
        should_process = trip.route_id in self._routes_to_process

        # Log trip information
        LoggerHelper.log_debug(
            [
                "Received Trip ID",
                trip.trip_id,
                "Route ID:",
                trip.route_id,
                "direction ID",
                str(trip.direction_id),
                "Start Time:",
                trip.start_time,
                "Start Date:",
                trip.start_date,
                "Processed:",
                should_process,
            ],
            1,
        )

        if not should_process:
            return

        # Process route ID
        route_id = self._data_processor.process_route_id(trip.route_id)
        direction_id = (
            str(trip.direction_id)
            if trip.direction_id is not None
            else DEFAULT_DIRECTION
        )

        # Initialize nested dictionaries
        if route_id not in departure_times:
            departure_times[route_id] = {}
        if direction_id not in departure_times[route_id]:
            departure_times[route_id][direction_id] = {}

        # Process each stop in the trip update
        for stop in entity.trip_update.stop_time_update:
            if stop.stop_id in self._routes_to_process[trip.route_id].get(
                direction_id, {}
            ):
                self._process_stop_update(
                    stop,
                    route_id,
                    direction_id,
                    trip.trip_id,
                    departure_times,
                    vehicle_positions,
                )

    def _process_stop_update(
        self,
        stop: Any,
        route_id: str,
        direction_id: str,
        trip_id: str,
        departure_times: Dict,
        vehicle_positions: Dict[str, Any],
    ) -> None:
        """Process a single stop time update."""
        stop_id = stop.stop_id

        # Initialize stop list if needed
        if stop_id not in departure_times[route_id][direction_id]:
            departure_times[route_id][direction_id][stop_id] = []

        # Extract stop time
        stop_time = self._data_processor.extract_stop_time(stop)

        LoggerHelper.log_debug(
            [
                "Stop:",
                stop_id,
                "Stop Sequence:",
                str(stop.stop_sequence),
                "Stop Time:",
                str(stop_time),
            ],
            2,
        )

        # Only process future departures
        if self._data_processor.is_future_departure(stop_time):
            LoggerHelper.log_debug(
                [
                    "Adding route ID",
                    route_id,
                    "trip ID",
                    trip_id,
                    "direction ID",
                    direction_id,
                    "stop ID",
                    stop_id,
                    "stop time",
                    str(stop_time),
                ],
                3,
            )

            details = StopDetails(
                datetime.fromtimestamp(stop_time), vehicle_positions.get(trip_id), True
            )
            departure_times[route_id][direction_id][stop_id].append(details)

    def _apply_static_fallback(self, departure_times: Dict) -> None:
        """Apply static GTFS fallback for routes with insufficient real-time data."""
        LoggerHelper.log_debug(
            ["Applying static fallback for routes with insufficient data"],
            logger=_LOGGER,
        )

        for route_id in departure_times:
            for direction_id in departure_times[route_id]:
                for stop_id in departure_times[route_id][direction_id]:
                    real_time_services = departure_times[route_id][direction_id][
                        stop_id
                    ]

                    # Only apply fallback if we have fewer than real-time departures than next_bus_limit
                    if len(real_time_services) < self._next_bus_limit:
                        static_services = self._static_processor.get_static_departures(
                            route_id, direction_id, stop_id
                        )
                        merged_services = (
                            self._static_processor.merge_real_time_and_static(
                                real_time_services, static_services
                            )
                        )

                        departure_times[route_id][direction_id][
                            stop_id
                        ] = merged_services

                        LoggerHelper.log_debug(
                            [
                                f"Merged services: {len(real_time_services)} real-time + {len(static_services)} static = {len(merged_services)} total"
                            ],
                            2,
                        )

    def _sort_departure_times(self, departure_times: Dict) -> None:
        """Sort all departure times by arrival time."""
        for route in departure_times:
            for direction in departure_times[route]:
                for stop in departure_times[route][direction]:
                    departure_times[route][direction][stop].sort(
                        key=lambda t: t.arrival_time
                    )

    def _get_vehicle_positions(self) -> Dict[str, Any]:
        """Get vehicle positions from the GTFS feed."""
        positions = {}

        try:
            feed_entities = self._feed_client.fetch_feed_entities(
                self._vehicle_position_url, "vehicle positions"
            )

            for entity in feed_entities:
                vehicle = entity.vehicle

                if not vehicle.trip.trip_id:
                    # Vehicle is not in service
                    continue

                LoggerHelper.log_debug(
                    [
                        "Adding position for trip ID",
                        vehicle.trip.trip_id,
                        "position latitude",
                        str(vehicle.position.latitude),
                        "longitude",
                        str(vehicle.position.longitude),
                    ],
                    2,
                )

                positions[vehicle.trip.trip_id] = vehicle.position

        except GTFSFeedError as e:
            LoggerHelper.log_error(
                [f"Error getting vehicle positions: {e}"], logger=_LOGGER
            )

        return positions

    def add_route_to_process(
        self, route_id: str, direction_id: str, stop_id: str
    ) -> None:
        """Add a route/direction/stop combination to the list to process."""
        if route_id not in self._routes_to_process:
            self._routes_to_process[route_id] = {}
        if direction_id not in self._routes_to_process[route_id]:
            self._routes_to_process[route_id][direction_id] = {}
        if stop_id not in self._routes_to_process[route_id][direction_id]:
            self._routes_to_process[route_id][direction_id][stop_id] = True

    def set_next_bus_limit(self, limit: int) -> None:
        """Set the next bus limit for static fallback processing."""
        self._next_bus_limit = limit
