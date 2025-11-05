import logging
from datetime import datetime, timedelta

import homeassistant.helpers.config_validation as cv
import homeassistant.util.dt as dt_util
import requests
import voluptuous as vol
from google.transit import gtfs_realtime_pb2
from homeassistant.components.sensor import PLATFORM_SCHEMA
from homeassistant.const import ATTR_LATITUDE, ATTR_LONGITUDE, CONF_NAME
from homeassistant.helpers.entity import Entity
from homeassistant.util import Throttle

_LOGGER = logging.getLogger(__name__)

ATTR_STOP_ID = "Stop ID"
ATTR_ROUTE = "Route"
ATTR_DIRECTION_ID = "Direction ID"
ATTR_DUE_IN = "Due in"
ATTR_DUE_AT = "Due at"
ATTR_NEXT_UP = "Next Service"
ATTR_ICON = "Icon"

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

DEFAULT_SERVICE = "Service"
DEFAULT_ICON = "mdi:bus"
DEFAULT_DIRECTION = "0"
DEFAULT_API_KEY_HEADER_NAME = 'Authorization'
DEFAULT_NEXT_BUS_LIMIT = 1

MIN_TIME_BETWEEN_UPDATES = timedelta(seconds=60)
TIME_STR_FORMAT = "%H:%M"

PLATFORM_SCHEMA = PLATFORM_SCHEMA.extend(
    {
        vol.Required(CONF_TRIP_UPDATE_URL): cv.string,
        vol.Optional(CONF_API_KEY): cv.string,
        vol.Optional(CONF_X_API_KEY): cv.string,
        vol.Optional(
            CONF_API_KEY_HEADER_NAME,
            default=DEFAULT_API_KEY_HEADER_NAME, # type: ignore
        ): cv.string,
        vol.Optional(CONF_VEHICLE_POSITION_URL): cv.string,
        vol.Optional(CONF_ROUTE_DELIMITER): cv.string,
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


def due_in_minutes(timestamp):
    """Get the remaining minutes from now until a given datetime object."""
    # Use local time instead of UTC to match GTFS timestamps which are in local time
    now_local = datetime.now()
    diff = timestamp - now_local
    return int(diff.total_seconds() / 60)


def log_info(data: list, indent_level: int) -> None:
    indents = "   " * indent_level
    info_str = f"{indents}{': '.join(str(x) for x in data)}"
    _LOGGER.info(info_str)


def log_error(data: list, indent_level: int) -> None:
    indents = "   " * indent_level
    info_str = f"{indents}{': '.join(str(x) for x in data)}"
    _LOGGER.error(info_str)


def log_debug(data: list, indent_level: int) -> None:
    indents = "   " * indent_level
    info_str = f"{indents}{' '.join(str(x) for x in data)}"
    _LOGGER.debug(info_str)


def setup_platform(hass, config, add_devices, discovery_info=None):
    """Get the public transport sensor."""

    data = PublicTransportData(
        config.get(CONF_TRIP_UPDATE_URL),
        config.get(CONF_VEHICLE_POSITION_URL),
        config.get(CONF_ROUTE_DELIMITER),
        config.get(CONF_API_KEY),
        config.get(CONF_X_API_KEY),
        config.get(CONF_API_KEY_HEADER_NAME),
    )
    sensors = []
    for departure in config.get(CONF_DEPARTURES):
        next_bus_limit = departure.get(CONF_NEXT_BUS_LIMIT, DEFAULT_NEXT_BUS_LIMIT)
        
        # Create multiple sensors for each next bus/service
        for bus_index in range(next_bus_limit):
            sensor_name = departure.get(CONF_NAME)
            if next_bus_limit > 1:
                if bus_index == 0:
                    sensor_name = f"{sensor_name} Next"
                else:
                    sensor_name = f"{sensor_name} Next {bus_index + 1}"
            
            sensors.append(
                PublicTransportSensor(
                    data,
                    departure.get(CONF_STOP_ID),
                    departure.get(CONF_ROUTE),
                    departure.get(CONF_DIRECTION_ID),
                    departure.get(CONF_ICON),
                    departure.get(CONF_SERVICE_TYPE),
                    sensor_name,
                    bus_index,  # Add bus index to track which bus this sensor represents
                )
            )

    add_devices(sensors)


def get_gtfs_feed_entities(url: str, headers, label: str):
    feed = gtfs_realtime_pb2.FeedMessage()  # type: ignore

    # TODO add timeout to requests call
    response = requests.get(url, headers=headers, timeout=20)
    if response.status_code == 200:
        log_info([f"Successfully updated {label}", response.status_code], 0)
    else:
        log_error(
            [
                f"Updating {label} got",
                response.status_code,
                response.content,
            ],
            0,
        )

    feed.ParseFromString(response.content)
    return feed.entity


class PublicTransportSensor(Entity):
    """Implementation of a public transport sensor."""

    def __init__(self, data, stop, route, direction, icon, service_type, name, bus_index=0):
        """Initialize the sensor."""
        self.data = data
        self._name = name
        self._stop = stop
        self._route = route
        self._direction = direction
        self._icon = icon
        self._service_type = service_type
        self._bus_index = bus_index  # Track which bus in the sequence this sensor represents
        self.update()

    @property
    def name(self):
        return self._name

    @property
    def unique_id(self):
        """Return a unique ID for this sensor."""
        return f"gtfs_rt_{self._route}_{self._stop}_{self._direction}_{self._bus_index}"

    def _get_next_services(self):
        return (
            self.data.info.get(self._route, {})
            .get(self._direction, {})
            .get(self._stop, [])
        )

    @property
    def state(self):
        """Return the state of the sensor."""
        next_services = self._get_next_services()
        return (
            due_in_minutes(next_services[self._bus_index].arrival_time)
            if len(next_services) > self._bus_index
            else "-"
        )

    @property
    def extra_state_attributes(self):
        """Return the state attributes."""
        next_services = self._get_next_services()
        current_service_index = self._bus_index
        next_service_index = self._bus_index + 1
        
        ATTR_NEXT_UP = "Next " + self._service_type
        attrs = {
            ATTR_DUE_IN: self.state,
            ATTR_STOP_ID: self._stop,
            ATTR_ROUTE: self._route,
            ATTR_DIRECTION_ID: self._direction,
            "Bus Index": self._bus_index + 1,  # Human-readable index (1-based)
        }
        
        # Current service information
        if len(next_services) > current_service_index:
            current_service = next_services[current_service_index]
            attrs[ATTR_DUE_AT] = current_service.arrival_time.strftime(TIME_STR_FORMAT)
            
            if current_service.position:
                attrs[ATTR_LATITUDE] = current_service.position.latitude
                attrs[ATTR_LONGITUDE] = current_service.position.longitude
        
        # Next service information (for this specific sensor)
        if len(next_services) > next_service_index:
            next_service = next_services[next_service_index]
            attrs[ATTR_NEXT_UP] = next_service.arrival_time.strftime(TIME_STR_FORMAT)
        else:
            attrs[ATTR_NEXT_UP] = "-"
            
        return attrs

    @property
    def unit_of_measurement(self):
        """Return the unit this state is expressed in."""
        return "min"

    @property
    def icon(self):
        return self._icon

    @property
    def service_type(self):
        return self._service_type

    def update(self):
        """Get the latest data from opendata.ch and update the states."""
        self.data.update()
        log_info(["Sensor Update:"], 0)
        log_info(["Name", self._name], 1)
        log_info(["Bus Index", self._bus_index + 1], 1)
        log_info([ATTR_ROUTE, self._route], 1)
        log_info([ATTR_STOP_ID, self._stop], 1)
        log_info([ATTR_DIRECTION_ID, self._direction], 1)
        log_info([ATTR_ICON, self._icon], 1)
        log_info(["Service Type", self._service_type], 1)
        log_info(["unit_of_measurement", self.unit_of_measurement], 1)
        log_info([ATTR_DUE_IN, self.state], 1)

        try:
            log_info(
                [ATTR_DUE_AT, self.extra_state_attributes[ATTR_DUE_AT]], 1
            )
        except KeyError:
            log_info([ATTR_DUE_AT, "not defined"], 1)

        try:
            log_info(
                [ATTR_LATITUDE, self.extra_state_attributes[ATTR_LATITUDE]], 1
            )
        except KeyError:
            log_info([ATTR_LATITUDE, "not defined"], 1)

        try:
            log_info(
                [ATTR_LONGITUDE, self.extra_state_attributes[ATTR_LONGITUDE]],
                1,
            )
        except KeyError:
            log_info([ATTR_LONGITUDE, "not defined"], 1)

        try:
            log_info(
                [
                    f"Next {self._service_type}",
                    self.extra_state_attributes["Next " + self._service_type],
                ],
                1,
            )
        except KeyError:
            log_info(["Next " + self._service_type, "not defined"], 1)


class PublicTransportData(object):
    """The Class for handling the data retrieval."""

    def __init__(
        self,
        trip_update_url,
        vehicle_position_url="",
        route_delimiter=None,
        api_key=None,
        x_api_key=None,
        api_key_header=None,
    ):
        """Initialize the info object."""
        self._trip_update_url = trip_update_url
        self._vehicle_position_url = vehicle_position_url
        self._route_delimiter = route_delimiter
        if api_key is not None:
            self._headers = {api_key_header: api_key}
        elif x_api_key is not None:
            self._headers = {"x-api-key": x_api_key}
        else:
            self._headers = None
        self.info = {}

    @Throttle(MIN_TIME_BETWEEN_UPDATES)
    def update(self):
        log_info(["trip_update_url", self._trip_update_url], 0)
        log_info(["vehicle_position_url", self._vehicle_position_url], 0)
        log_info(["route_delimiter", self._route_delimiter], 0)
        log_info(["header", self._headers], 0)

        positions = (
            self._get_vehicle_positions()
            if self._vehicle_position_url
            else {}
        )
        self._update_route_statuses(positions)

    def _update_route_statuses(self, vehicle_positions):
        """Get the latest data."""

        class StopDetails:
            def __init__(self, arrival_time, position):
                self.arrival_time = arrival_time
                self.position = position

        departure_times = {}

        feed_entities = get_gtfs_feed_entities(
            url=self._trip_update_url, headers=self._headers, label="trip data"
        )

        for entity in feed_entities:
            if entity.HasField("trip_update"):
                # If delimiter specified split the route ID in the gtfs rt feed
                log_debug(
                    [
                        "Received Trip ID",
                        entity.trip_update.trip.trip_id,
                        "Route ID:",
                        entity.trip_update.trip.route_id,
                        "direction ID",
                        entity.trip_update.trip.direction_id,
                        "Start Time:",
                        entity.trip_update.trip.start_time,
                        "Start Date:",
                        entity.trip_update.trip.start_date,
                    ],
                    1,
                )
                if self._route_delimiter is not None:
                    route_id_split = entity.trip_update.trip.route_id.split(
                        self._route_delimiter
                    )
                    if route_id_split[0] == self._route_delimiter:
                        route_id = entity.trip_update.trip.route_id
                    else:
                        route_id = route_id_split[0]
                    log_debug(
                        [
                            "Feed Route ID",
                            entity.trip_update.trip.route_id,
                            "changed to",
                            route_id,
                        ],
                        1,
                    )

                else:
                    route_id = entity.trip_update.trip.route_id

                if route_id not in departure_times:
                    departure_times[route_id] = {}

                if entity.trip_update.trip.direction_id is not None:
                    direction_id = str(entity.trip_update.trip.direction_id)
                else:
                    direction_id = DEFAULT_DIRECTION
                if direction_id not in departure_times[route_id]:
                    departure_times[route_id][direction_id] = {}

                for stop in entity.trip_update.stop_time_update:
                    stop_id = stop.stop_id
                    if not departure_times[route_id][direction_id].get(
                        stop_id
                    ):
                        departure_times[route_id][direction_id][stop_id] = []
                    # Use stop arrival time;
                    # fall back on departure time if not available
                    if stop.arrival.time == 0:
                        stop_time = stop.departure.time
                    else:
                        stop_time = stop.arrival.time
                    log_debug(
                        [
                            "Stop:",
                            stop_id,
                            "Stop Sequence:",
                            stop.stop_sequence,
                            "Stop Time:",
                            stop_time,
                        ],
                        2,
                    )
                    # Ignore arrival times in the past
                    if due_in_minutes(datetime.fromtimestamp(stop_time)) >= 0:
                        log_debug(
                            [
                                "Adding route ID",
                                route_id,
                                "trip ID",
                                entity.trip_update.trip.trip_id,
                                "direction ID",
                                entity.trip_update.trip.direction_id,
                                "stop ID",
                                stop_id,
                                "stop time",
                                stop_time,
                            ],
                            3,
                        )

                        details = StopDetails(
                            datetime.fromtimestamp(stop_time),
                            vehicle_positions.get(
                                entity.trip_update.trip.trip_id
                            ),
                        )
                        departure_times[route_id][direction_id][
                            stop_id
                        ].append(details)

        # Sort by arrival time
        for route in departure_times:
            for direction in departure_times[route]:
                for stop in departure_times[route][direction]:
                    departure_times[route][direction][stop].sort(
                        key=lambda t: t.arrival_time
                    )

        self.info = departure_times

    def _get_vehicle_positions(self):
        positions = {}
        feed_entities = get_gtfs_feed_entities(
            url=self._vehicle_position_url,
            headers=self._headers,
            label="vehicle positions",
        )

        for entity in feed_entities:
            vehicle = entity.vehicle

            if not vehicle.trip.trip_id:
                # Vehicle is not in service
                continue
            log_debug(
                [
                    "Adding position for trip ID",
                    vehicle.trip.trip_id,
                    "position latitude",
                    vehicle.position.latitude,
                    "longitude",
                    vehicle.position.longitude,
                ],
                2,
            )

            positions[vehicle.trip.trip_id] = vehicle.position

        return positions
