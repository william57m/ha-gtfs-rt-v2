import logging
import requests

from datetime import datetime
from google.transit import gtfs_realtime_pb2
from typing import Dict, List, Optional, Any

from .logger_helper import LoggerHelper

_LOGGER = logging.getLogger(__name__)


def due_in_minutes(timestamp):
    now_local = datetime.now()
    diff = timestamp - now_local
    return int(diff.total_seconds() / 60)


class GTFSFeedError(Exception):

    pass


class GTFSDataProcessor:
    """Handles GTFS feed data processing and parsing."""

    def __init__(self, route_delimiter: Optional[str] = None):
        self.route_delimiter = route_delimiter

    def process_route_id(self, original_route_id: str) -> str:
        if self.route_delimiter is None:
            return original_route_id

        route_id_split = original_route_id.split(self.route_delimiter)
        if route_id_split[0] == self.route_delimiter:
            return original_route_id
        else:
            processed_id = route_id_split[0]
            return processed_id

    def extract_stop_time(self, stop) -> int:
        return stop.arrival.time if stop.arrival.time != 0 else stop.departure.time

    def is_future_departure(self, timestamp: int) -> bool:
        return due_in_minutes(datetime.fromtimestamp(timestamp)) >= 0


class GTFSFeedClient:
    def __init__(
        self,
        api_key: Optional[str],
        api_key_header: Optional[str],
    ):
        self.headers = self._build_headers(api_key, api_key_header)

    def _build_headers(
        self,
        api_key: Optional[str],
        api_key_header: Optional[str],
    ) -> Optional[Dict[str, str]]:
        """Build HTTP headers based on provided API keys."""
        if api_key is not None and api_key_header is not None:
            return {api_key_header: api_key}
        return None

    def fetch_feed_entities(self, url: str, label: str) -> List[Any]:
        try:
            feed = gtfs_realtime_pb2.FeedMessage()
            response = requests.get(url, headers=self.headers, timeout=20)

            if response.status_code == 200:
                LoggerHelper.log_debug(
                    [f"Successfully updated {label}", str(response.status_code)]
                )
            else:
                LoggerHelper.log_error(
                    [f"Updating {label} failed", str(response.status_code)],
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
