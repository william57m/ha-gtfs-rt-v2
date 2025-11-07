"""
Script for quicker and easier testing of GTFS-RT-V2 outside of Home Assistant.
Usage: test.py -f <yaml file> -d INFO|DEBUG { -l <outfile log file> }

<yaml file> contains the sensor configuration from HA.
See test_translink.yaml for example
<output file> is a text file for output
"""

import argparse
import asyncio
import logging
import sys
import yaml

from schema import Optional, Schema, SchemaError

from .const import (
    CONF_API_KEY,
    CONF_API_KEY_HEADER_NAME,
    CONF_DEPARTURES,
    CONF_DIRECTION_ID,
    CONF_ENABLE_STATIC_FALLBACK,
    CONF_ICON,
    CONF_NEXT_BUS_LIMIT,
    CONF_ROUTE,
    CONF_ROUTE_DELIMITER,
    CONF_SERVICE_TYPE,
    CONF_STATIC_GTFS_URL,
    CONF_STOP_ID,
    CONF_TRIP_UPDATE_URL,
    CONF_UPDATE_INTERVAL,
    CONF_VEHICLE_POSITION_URL,
    DEFAULT_DIRECTION,
    DEFAULT_ICON,
    DEFAULT_NEXT_BUS_LIMIT,
    DEFAULT_SERVICE,
    DEFAULT_UPDATE_INTERVAL,
)
from .sensor import PublicTransportData, PublicTransportSensor

sys.path.append("lib")
_LOGGER = logging.getLogger(__name__)

CONF_NAME = "name"

PLATFORM_SCHEMA = Schema(
    {
        CONF_TRIP_UPDATE_URL: str,
        Optional(CONF_API_KEY): str,
        Optional(CONF_API_KEY_HEADER_NAME): str,
        Optional(CONF_VEHICLE_POSITION_URL): str,
        Optional(CONF_ROUTE_DELIMITER): str,
        Optional(CONF_UPDATE_INTERVAL): int,
        Optional(CONF_STATIC_GTFS_URL): str,
        Optional(CONF_ENABLE_STATIC_FALLBACK): bool,
        CONF_DEPARTURES: [
            {
                CONF_NAME: str,
                CONF_STOP_ID: str,
                CONF_ROUTE: str,
                Optional(CONF_DIRECTION_ID): str,
                Optional(CONF_SERVICE_TYPE): str,
                Optional(CONF_ICON): str,
                Optional(CONF_NEXT_BUS_LIMIT): int,
            }
        ],
    }
)


async def main():
    parser = argparse.ArgumentParser(description="Test script for ha-gtfs-rt-v2")
    parser.add_argument(
        "-f", "--file", dest="file", help="Config file to use", metavar="FILE"
    )
    parser.add_argument(
        "-l", "--log", dest="log", help="Output file for log", metavar="FILE"
    )
    parser.add_argument(
        "-d",
        "--debug",
        dest="debug",
        help="Debug level: INFO (default) or DEBUG",
    )
    args = vars(parser.parse_args())

    if args["file"] is None:
        raise ValueError("Config file spec required.")
    if args["debug"] is None:
        DEBUG_LEVEL = "INFO"
    elif args["debug"].upper() == "INFO" or args["debug"].upper() == "DEBUG":
        DEBUG_LEVEL = args["debug"].upper()
    else:
        raise ValueError("Debug level must be INFO or DEBUG")
    if args["log"] is None:
        logging.basicConfig(level=DEBUG_LEVEL)
    else:
        logging.basicConfig(filename=args["log"], filemode="w", level=DEBUG_LEVEL)

    with open(args["file"], "r") as test_yaml:
        configuration = yaml.safe_load(test_yaml)
    try:
        PLATFORM_SCHEMA.validate(configuration)
        logging.info("Input file configuration is valid.")

        data = PublicTransportData(
            configuration.get(CONF_TRIP_UPDATE_URL),
            configuration.get(CONF_VEHICLE_POSITION_URL),
            configuration.get(CONF_ROUTE_DELIMITER),
            configuration.get(CONF_API_KEY, None),
            configuration.get(CONF_API_KEY_HEADER_NAME, None),
            configuration.get(CONF_UPDATE_INTERVAL, DEFAULT_UPDATE_INTERVAL),
            configuration.get(CONF_STATIC_GTFS_URL),
            configuration.get(CONF_ENABLE_STATIC_FALLBACK, False),
        )
        await data.load_gtfs_static_data()

        sensors = []
        for departure in configuration[CONF_DEPARTURES]:
            next_bus_limit = departure.get(CONF_NEXT_BUS_LIMIT, DEFAULT_NEXT_BUS_LIMIT)

            _LOGGER.info(
                "Adding Sensors: Name: {}, route_id: {}, direction_id: {}, next_bus_limit: {}".format(
                    departure[CONF_NAME],
                    departure[CONF_ROUTE],
                    departure[CONF_STOP_ID],
                    next_bus_limit,
                )
            )

            # Create multiple sensors for each next bus/service
            for bus_index in range(next_bus_limit):
                sensor_name = departure.get(CONF_NAME)
                if next_bus_limit > 1:
                    if bus_index == 0:
                        sensor_name = f"{sensor_name} Next"
                    else:
                        sensor_name = f"{sensor_name} Next {bus_index + 1}"

                _LOGGER.info(f"Creating sensor {bus_index + 1}: {sensor_name}")

                sensor = PublicTransportSensor(
                    data=data,
                    stop_id=departure.get(CONF_STOP_ID),
                    route=departure.get(CONF_ROUTE),
                    direction=departure.get(CONF_DIRECTION_ID, DEFAULT_DIRECTION),
                    icon=departure.get(CONF_ICON, DEFAULT_ICON),
                    service_type=departure.get(CONF_SERVICE_TYPE, DEFAULT_SERVICE),
                    name=sensor_name,
                    bus_index=bus_index,
                    next_bus_limit=next_bus_limit,
                )
                sensor.update()
                sensors.append(sensor)

    except SchemaError as se:
        logging.info("Input file configuration invalid: {}".format(se))


if __name__ == "__main__":
    asyncio.run(main())
