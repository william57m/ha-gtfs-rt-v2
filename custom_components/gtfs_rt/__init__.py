from .const import (
    CONF_API_KEY,
    CONF_API_KEY_HEADER_NAME,
    CONF_TRIP_UPDATE_URL,
    CONF_VEHICLE_POSITION_URL,
    CONF_ROUTE_DELIMITER,
    CONF_UPDATE_INTERVAL,
    CONF_STATIC_GTFS_URL,
    CONF_ENABLE_STATIC_FALLBACK,
    DEFAULT_UPDATE_INTERVAL,
)
from .sensor import PublicTransportData, SensorFactory


async def async_setup_platform(hass, config, add_entities, discovery_info=None):
    data = PublicTransportData(
        config.get(CONF_TRIP_UPDATE_URL),
        config.get(CONF_VEHICLE_POSITION_URL),
        config.get(CONF_ROUTE_DELIMITER),
        config.get(CONF_API_KEY),
        config.get(CONF_API_KEY_HEADER_NAME),
        config.get(CONF_UPDATE_INTERVAL, DEFAULT_UPDATE_INTERVAL),
        config.get(CONF_STATIC_GTFS_URL),
        config.get(CONF_ENABLE_STATIC_FALLBACK, False),
    )

    # Create sensors
    sensors = SensorFactory.create_sensors_from_config(config, data)
    add_entities(sensors)

    # Fetch static GTFS data
    # hass.create_task(data.load_gtfs_static_data())
