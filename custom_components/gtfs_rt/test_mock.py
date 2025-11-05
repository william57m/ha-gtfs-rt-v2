#!/usr/bin/env python3
"""
Mock test script for GTFS-RT component without full Home Assistant
"""
import logging
import yaml
from unittest.mock import MagicMock, patch
import sys
import os

# Mock Home Assistant modules
sys.modules['homeassistant'] = MagicMock()
sys.modules['homeassistant.helpers'] = MagicMock()
sys.modules['homeassistant.helpers.config_validation'] = MagicMock()
sys.modules['homeassistant.util'] = MagicMock()
sys.modules['homeassistant.util.dt'] = MagicMock()
sys.modules['homeassistant.components'] = MagicMock()
sys.modules['homeassistant.components.sensor'] = MagicMock()
sys.modules['homeassistant.const'] = MagicMock()
sys.modules['homeassistant.helpers.entity'] = MagicMock()

# Set up basic constants
sys.modules['homeassistant.const'].ATTR_LATITUDE = 'latitude'
sys.modules['homeassistant.const'].ATTR_LONGITUDE = 'longitude'
sys.modules['homeassistant.const'].CONF_NAME = 'name'

# Mock the platform schema
sys.modules['homeassistant.components.sensor'].PLATFORM_SCHEMA = {}

# Mock entity
class MockEntity:
    def __init__(self):
        pass

sys.modules['homeassistant.helpers.entity'].Entity = MockEntity

# Mock throttle decorator
def mock_throttle(time_delta):
    def decorator(func):
        return func
    return decorator

sys.modules['homeassistant.util'].Throttle = mock_throttle

# Now import your sensor
from sensor import PublicTransportData, PublicTransportSensor

def test_gtfs_component():
    """Test basic functionality of the GTFS component"""
    
    # Load test configuration
    with open('test_translink.yaml', 'r') as f:
        config = yaml.safe_load(f)
    
    print("Configuration loaded:")
    print(yaml.dump(config, default_flow_style=False))
    
    # Create data object
    data = PublicTransportData(
        config.get('trip_update_url'),
        config.get('vehicle_position_url'),
        config.get('route_delimiter'),
        config.get('api_key'),
        config.get('x_api_key'),
        config.get('api_key_header')
    )
    
    print(f"Data object created with URL: {config.get('trip_update_url')}")
    
    # Create sensors
    sensors = []
    for departure in config.get('departures', []):
        sensor = PublicTransportSensor(
            data,
            departure.get('stopid'),
            departure.get('route'),
            departure.get('directionid', '0'),
            departure.get('icon', 'mdi:bus'),
            departure.get('service_type', 'bus'),
            departure.get('name')
        )
        sensors.append(sensor)
        print(f"Created sensor: {departure.get('name')}")
    
    print(f"Total sensors created: {len(sensors)}")
    
    # Test data update (this might fail due to network/API requirements)
    try:
        data.update()
        print("Data update successful")
    except Exception as e:
        print(f"Data update failed (expected): {e}")
    
    return sensors

if __name__ == "__main__":
    logging.basicConfig(level=logging.DEBUG)
    test_gtfs_component()