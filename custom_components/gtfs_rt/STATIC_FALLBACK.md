# Static GTFS Fallback Feature

## Overview

The static GTFS fallback feature allows the GTFS-RT sensor to supplement real-time departure information with static schedule data when there aren't enough real-time departures available. This ensures users always see a minimum number of upcoming departures, even when real-time data is sparse or unavailable.

## Configuration

Add the following parameters to your sensor configuration:

```yaml
# Enable static fallback for demonstration
enable_static_fallback: true
static_gtfs_url: 'https://example.com/gtfs.zip'  # URL to static GTFS feed (optional)
```

### Configuration Parameters

- `enable_static_fallback` (optional, default: `false`): Enable the static fallback feature
- `static_gtfs_url` (optional): URL to a static GTFS feed zip file

## How It Works

1. **Real-time Data Collection**: The sensor first collects real-time departure information from the GTFS-RT feed
2. **Departure Count Check**: For each route/stop/direction combination, it checks if there are at least 3 future departures
3. **Static Fallback**: If there are fewer than 3 departures, it adds static departures
4. **Conflict Resolution**: Static departures are filtered out if they're within 2 minutes of an existing real-time departure
5. **Sorting**: All departures (real-time and static) are sorted by arrival time

## Sensor Attributes

When static fallback is enabled, sensor attributes include additional information:

- `Is Real-time`: `true` for real-time data, `false` for static schedule data  
- `Data Source`: Shows "Real-time" or "Static Schedule"
- `Trip ID`: Includes trip identifier (real trips have actual IDs, static trips have generated IDs like "static_16718_0")

## Example Output

```
Due in: 12 minutes
Due at: 11:25
Is Real-time: true
Data Source: Real-time
Trip ID: 12345_actual_trip
```

vs.

```
Due in: 27 minutes  
Due at: 11:40
Is Real-time: false
Data Source: Static Schedule
Trip ID: static_16718_1
```

## Implementation Notes

### Future Enhancements
- Full static GTFS parsing from zip files
- Actual schedule lookup based on service calendar and stop times
- Support for service exceptions and holiday schedules
- Configurable minimum departure count
- Better conflict resolution algorithms

## Testing

Use the test configuration `test_static_fallback.yaml` to see the feature in action:

```bash
python3 test.py -f test_static_fallback.yaml
```

The log output will show messages like:
```
INFO:sensor:Added 2 static departures for route 16718, stop 12813, direction 0
```

This indicates that static departures were successfully added to supplement the real-time data.

## Benefits

1. **Consistent User Experience**: Users always see upcoming departures, even when real-time data is incomplete
2. **Service Reliability**: Provides fallback when GTFS-RT feeds have gaps or issues
3. **Better Planning**: Helps users plan trips even with limited real-time information
4. **Graceful Degradation**: System continues to work even when real-time feeds are problematic

## Technical Details

- Static departures are marked with `is_realtime=False` 
- Generated trip IDs follow the pattern: `static_{route_id}_{sequence_number}`
- Conflict detection uses a 2-minute window around existing real-time departures
- All departure times are in local time to match GTFS-RT behavior