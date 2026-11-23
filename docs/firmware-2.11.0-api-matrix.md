# Firmware-indexed API compatibility: 2026-11-23

Source: user-provided read-only `probe_api.py` capture generated at **2026-11-23 14:50:13 UTC**. **11/11 controllers online**. Each controller reported `/xled/v1/fw/version` = **2.11.0**. This is observational evidence, not a universal firmware compatibility contract.

No local IPs, MACs, UUIDs, client IDs, Wi-Fi details or auth tokens are included in this report.

## Hardware coverage

| Product code | FW family | Hardware versions observed | Controllers |
|---|---|---|---:|
| `TWS400SPP` | G | 100, 300, 308 | 6 |
| `TWS600SPP1` | AB | 308 | 1 |
| `TWI190SPP` | G | 100 | 1 |
| `TWP500SPP` | R | 400 | 1 |
| `TWW210SPP` | G | 300, 302 | 2 |
| **Total** | | | **11** |

## GET endpoint compatibility

`OK` means JSON response with application code `1000`; `404` means HTTP 404; `non-JSON` means the current JSON-only client could not decode the response (not evidence the API is unsupported).

| GET path | OK | 404 | Non-JSON |
|---|---:|---:|---:|
| `/xled/v1/gestalt` | 11 | 0 | 0 |
| `/xled/v1/fw/version` | 11 | 0 | 0 |
| `/xled/v1/status` | 11 | 0 | 0 |
| `/xled/v1/device_name` | 11 | 0 | 0 |
| `/xled/v1/summary` | 11 | 0 | 0 |
| `/xled/v1/application/group` | 11 | 0 | 0 |
| `/xled/v1/application/mode` | 0 | 11 | 0 |
| `/xled/v1/led/layout/full` | 11 | 0 | 0 |
| `/xled/v1/led/mode` | 11 | 0 | 0 |
| `/xled/v1/led/config` | 11 | 0 | 0 |
| `/xled/v1/led/movie/config` | 11 | 0 | 0 |
| `/xled/v1/led/effects` | 11 | 0 | 0 |
| `/xled/v1/led/effects/current` | 11 | 0 | 0 |
| `/xled/v1/led/out/brightness` | 11 | 0 | 0 |
| `/xled/v1/led/color` | 11 | 0 | 0 |
| `/xled/v1/led/out/saturation` | 11 | 0 | 0 |
| `/xled/v1/led/movies` | 0 | 11 | 0 |
| `/xled/v1/led/movies/current` | 0 | 11 | 0 |
| `/xled/v1/led/driver_params` | 11 | 0 | 0 |
| `/xled/v1/movies/current` | 1 | 0 | 10 |
| `/xled/v1/playlist/current` | 0 | 0 | 11 |
| `/xled/v1/playlist` | 11 | 0 | 0 |
| `/xled/v1/timer` | 11 | 0 | 0 |
| `/xled/v1/network/status` | 11 | 0 | 0 |
| `/xled/v1/network/scan_results` | 11 | 0 | 0 |
| `/xled/v1/network/station` | 0 | 11 | 0 |
| `/xled/v1/network/ap` | 0 | 11 | 0 |
| `/xled/v1/mic/config` | 0 | 11 | 0 |
| `/xled/v1/mic/sample` | 0 | 11 | 0 |
| `/xled/v1/music/drivers/sets/current` | 11 | 0 | 0 |
| `/xled/v1/mqtt/config` | 11 | 0 | 0 |

**Total: 243 successful JSON responses out of 341 attempted GETs.** `GET /xled/v1/network/scan` was intentionally *not* attempted, to avoid triggering a scan.

## Confirmed response field structures (representative)

All keys shown below were observed in successful responses on this firmware; sensitive field values are omitted.

| Endpoint | Observed top-level fields |
|---|---|
| `/application/group` | `code`, `group{mode,compat_mode,sync_on_time,time_sync}`; when grouped also `size`, `offset` (slaves), `uid` |
| `/summary` | `code`, `color`, `filters[]`, `group`, `layout{uuid}`, `led_mode`, `music`, `timer` |
| `/led/layout/full` | `code`, `coordinates[]`, `source`, `synthesized`, `uuid` |
| `/led/config` | `code`, `strings[]` |
| `/led/out/brightness` | `code`, `mode`, `value` |
| `/led/color` | `code`, `red`, `green`, `blue`, `white`, `hue`, `saturation`, `value` |
| `/led/effects` | `code`, `effects_number`, `unique_ids[]` |
| `/led/driver_params` | `code`, `t0h`, `t0l`, `t1h`, `t1l`, `tendh`, `tendl` |
| `/network/status` | `code`, `mode`, `station{ip,mask,gw,ssid,rssi,connected_bssid,monitor_enabled}`, `ap{ip,ssid,enc,channel,max_connections,ssid_hidden}` |
| `/music/drivers/sets/current` | `code`, `driverset_id` |
| `/mqtt/config` | `code`, `broker_host`, `broker_port`, `client_id`, `keep_alive_interval`, `user` |

### Grouping findings from this capture

- The **Office Left** and **Office Right** devices both returned `group.mode = none`, with separate **3D** layouts of **210 LEDs each**. This particular capture is **post-ungroup**, not a snapshot of their active Area configuration.
- The five-controller combined display remains grouped: one master and four slaves, `group.size = 2200`; slave offsets are **400, 800, 1200, 1600**. The master may omit an explicit `offset` field; treat its effective offset as zero.
- **Mid Right** reports `slave`, `size = 4900`, `offset = 4080`. Its master was not among these 11 configured controllers. Do not automatically regroup or overwrite this state.
- `mode=none` devices omit `uid`, `size`, `offset` in the returned group object.
- The read-only `application/group` endpoint is the clearest observed source of membership metadata, and should be preferred for preflight validation over depending only on `summary`.

### Limitations and follow-up

- The two `.../current` responses that did not decode as JSON require a client enhancement that records HTTP status, content type, body length and an optional **redacted** body preview. Do **not** categorise them as HTTP 404 or definitively unsupported.
- Firmware 2.11.0 is the only tested version. Different product codes and hardware versions were observed, but no multi-firmware compatibility conclusion can be drawn.
- The raw response ZIP contains LAN-specific information and must remain private. This report contains only anonymised observations.
- This sweep used GET calls only. It does not prove that the separate ungroup/regroup Python writer restores the display or its app-managed name/effects.
