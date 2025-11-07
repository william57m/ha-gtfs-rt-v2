import csv
import io
import zipfile
import logging
import asyncio
import aiohttp

from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any

from .logger_helper import LoggerHelper
from .stop_details import StopDetails

_LOGGER = logging.getLogger(__name__)


class StaticGTFSProcessor:

    def __init__(self, static_gtfs_url: str):
        self._static_gtfs_url = static_gtfs_url
        self._static_data = {}
        self._static_data_cache_duration = timedelta(weeks=4)
        self._departures = {}
        self._last_fetch_time = None

    def get_static_departures(
        self, route_id: str, direction_id: str, stop_id: str
    ) -> List[StopDetails]:

        # Only return data if it's already loaded
        if self.has_data():
            return self._get_scheduled_departures(route_id, direction_id, stop_id)

        # If no data is available, log a message but don't block
        LoggerHelper.log_debug(
            ["Static GTFS data not available yet, skipping"],
            logger=_LOGGER,
        )

        return []

    def merge_real_time_and_static(
        self, real_time_services: List[StopDetails], static_services: List[StopDetails]
    ) -> List[StopDetails]:
        if not static_services:
            return real_time_services

        LoggerHelper.log_debug("merge_real_time_and_static")
        merged_services = list(real_time_services)

        # Add static services that don't conflict with real-time data
        for static_service in static_services:
            # Check if there's a real-time service within 5 minutes of this static time
            has_conflict = any(
                abs(
                    (
                        rt_service.arrival_time - static_service.arrival_time
                    ).total_seconds()
                )
                < 300
                for rt_service in real_time_services
            )

            if not has_conflict:
                merged_services.append(static_service)
                LoggerHelper.log_debug(
                    [
                        "Added static departure at",
                        static_service.arrival_time.strftime("%H:%M"),
                    ],
                    2,
                    logger=_LOGGER,
                )
            else:
                LoggerHelper.log_debug(
                    [
                        "Skipped static departure at",
                        static_service.arrival_time.strftime("%H:%M"),
                        "due to real-time conflict",
                    ],
                    2,
                    logger=_LOGGER,
                )

        # Sort merged services by arrival time
        merged_services.sort(key=lambda s: s.arrival_time)
        return merged_services

    def _is_data_fresh(self) -> bool:
        if not self._last_fetch_time or not self._static_data:
            return False
        return datetime.now() - self._last_fetch_time < self._static_data_cache_duration

    def has_data(self) -> bool:
        return bool(self._static_data)

    async def load_gtfs_data(self) -> None:
        LoggerHelper.log_info(
            [f"Loading GTFS data asynchronously from {self._static_gtfs_url}"],
            logger=_LOGGER,
        )

        try:
            # Download GTFS zip file using aiohttp
            timeout = aiohttp.ClientTimeout(total=30)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(self._static_gtfs_url) as response:
                    response.raise_for_status()
                    content = await response.read()

            LoggerHelper.log_info(
                [f"Content loaded"],
                logger=_LOGGER,
            )
            # Parse zip file (CPU-bound work, but relatively fast)
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, self._parse_gtfs_content, content)
            LoggerHelper.log_info(
                [f"Content unzipeped and parsed"],
                logger=_LOGGER,
            )

            self._last_fetch_time = datetime.now()
            LoggerHelper.log_info(
                [
                    f"GTFS data loaded asynchronously. Found {len(self._static_data.get('routes', {}))} routes"
                ],
                logger=_LOGGER,
            )
        except Exception as e:
            LoggerHelper.log_error(
                [f"Failed to load GTFS data asynchronously: {e}"], logger=_LOGGER
            )
            raise

    def _parse_gtfs_content(self, content: bytes) -> None:
        with zipfile.ZipFile(io.BytesIO(content)) as zip_file:
            routes = self._parse_csv_from_zip(zip_file, "routes.txt")
            routes_dict = {row["route_id"]: row for row in routes}

            calendar = self._parse_csv_from_zip(zip_file, "calendar.txt")
            calendar_dates = self._parse_csv_from_zip(zip_file, "calendar_dates.txt")

            trips = self._parse_csv_from_zip(zip_file, "trips.txt")
            trips_dict = {row["trip_id"]: row for row in trips}

            stop_times = self._parse_csv_from_zip(zip_file, "stop_times.txt")

            stops = self._parse_csv_from_zip(zip_file, "stops.txt")
            stops_dict = {row["stop_id"]: row for row in stops}

        self._static_data = {
            "routes": routes_dict,
            "trips": trips_dict,
            "stops": stops_dict,
            "calendar": calendar,
            "calendar_dates": calendar_dates,
            "stop_times": self._organize_stop_times(stop_times),
        }

    def _parse_csv_from_zip(
        self, zip_file: zipfile.ZipFile, filename: str
    ) -> List[Dict[str, str]]:
        try:
            with zip_file.open(filename) as csv_file:
                content = csv_file.read().decode("utf-8-sig")
                reader = csv.DictReader(io.StringIO(content))
                return list(reader)
        except KeyError:
            LoggerHelper.log_error(
                [f"File {filename} not found in GTFS zip"], logger=_LOGGER
            )
            return []

    def _organize_stop_times(
        self, stop_times: List[Dict[str, str]]
    ) -> Dict[str, List[Dict[str, str]]]:
        organized = {}
        for stop_time in stop_times:
            trip_id = stop_time["trip_id"]
            if trip_id not in organized:
                organized[trip_id] = []
            organized[trip_id].append(stop_time)

        # Sort stop times by stop_sequence
        for trip_id in organized:
            organized[trip_id].sort(key=lambda x: int(x.get("stop_sequence", 0)))

        return organized

    def _cache_scheduled_departures(
        self, route_id: str, direction_id: str, stop_id: str
    ) -> List[StopDetails]:

        LoggerHelper.log_info("_cache_scheduled_departures")

        # Find trips for this route and direction today
        today_services = self._get_active_service_ids()
        matching_trips = []
        for trip_id, trip_data in self._static_data["trips"].items():
            if (
                trip_data["route_id"] == route_id
                and trip_data.get("direction_id", "0") == str(direction_id)
                and trip_data["service_id"] in today_services
            ):
                matching_trips.append(trip_id)

        # Get stop times for matching trips
        departures = []
        for trip_id in matching_trips:
            if trip_id in self._static_data["stop_times"]:
                for stop_time in self._static_data["stop_times"][trip_id]:
                    if stop_time["stop_id"] == stop_id:
                        departure_time = self._parse_gtfs_time(
                            stop_time.get("departure_time")
                            or stop_time.get("arrival_time")
                        )
                        departures.append(
                            StopDetails(
                                arrival_time=departure_time,
                                position=None,
                                is_real_time=False,
                            )
                        )
        self._departures[(route_id, direction_id, stop_id)] = departures
        LoggerHelper.log_debug("end _cache_scheduled_departures")

    def _get_scheduled_departures(
        self, route_id: str, direction_id: str, stop_id: str
    ) -> List[StopDetails]:
        if (route_id, direction_id, stop_id) not in self._departures:
            self._cache_scheduled_departures(route_id, direction_id, stop_id)

        current_time = datetime.now()

        departures = []
        for departure in self._departures[(route_id, direction_id, stop_id)]:
            if departure.arrival_time > current_time:
                departures.append(departure)

        departures.sort(key=lambda x: x.arrival_time)
        LoggerHelper.log_info("departures")
        LoggerHelper.log_info(departures)
        return departures[:10]

    def _get_active_service_ids(self) -> List[str]:
        if not self._static_data:
            return []

        today = datetime.now()
        weekday = today.strftime("%A").lower()
        date_str = today.strftime("%Y%m%d")

        active_services = []

        # Check calendar.txt for regular service
        for calendar_entry in self._static_data["calendar"]:
            start_date = calendar_entry.get("start_date", "")
            end_date = calendar_entry.get("end_date", "")

            # Check if today is within service period
            if (
                start_date <= date_str <= end_date
                and calendar_entry.get(weekday) == "1"
            ):
                active_services.append(calendar_entry["service_id"])

        # Check calendar_dates.txt for exceptions
        for calendar_date in self._static_data["calendar_dates"]:
            if calendar_date.get("date") == date_str:
                service_id = calendar_date["service_id"]
                exception_type = calendar_date.get("exception_type", "1")

                if exception_type == "1":  # Service added
                    if service_id not in active_services:
                        active_services.append(service_id)
                elif exception_type == "2":  # Service removed
                    if service_id in active_services:
                        active_services.remove(service_id)

        return active_services

    def _parse_gtfs_time(self, time_str: Optional[str]) -> Optional[datetime]:
        """Parse GTFS time string (HH:MM:SS) into datetime object."""
        if not time_str:
            return None

        try:
            # GTFS time can exceed 24 hours (e.g., 25:30:00 for 1:30 AM next day)
            parts = time_str.split(":")
            hours = int(parts[0])
            minutes = int(parts[1])
            seconds = int(parts[2]) if len(parts) > 2 else 0

            # Calculate the actual datetime
            today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
            total_minutes = hours * 60 + minutes

            # Handle times that go past midnight
            if hours >= 24:
                total_minutes = total_minutes - (24 * 60)
                today = today + timedelta(days=1)

            departure_time = today + timedelta(minutes=total_minutes, seconds=seconds)
            return departure_time
        except (ValueError, IndexError) as e:
            LoggerHelper.log_error(
                [f"Failed to parse GTFS time '{time_str}': {e}"], logger=_LOGGER
            )
            return None
