"""Merge native inventory without dropping or renaming configured devices."""
def merge_controller_inventory(configured, native):
    result = [dict(d) for d in configured]
    ips = {d["ip"] for d in result}
    names = {d["name"].casefold() for d in result}
    for device in native:
        if device["ip"] in ips:
            continue
        d = dict(device)
        if d["name"].casefold() in names:
            # Ambiguous address change: never silently target a second device.
            continue
        result.append(d)
        ips.add(d["ip"])
        names.add(d["name"].casefold())
    return result
