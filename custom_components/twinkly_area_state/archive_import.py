"""Import immutable CLI 3D archives into HA storage without changing controllers."""
import hashlib
import json
import math
import re
from pathlib import Path

def digest(value, ascii=True):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
        ensure_ascii=ascii, allow_nan=False).encode("utf-8")).hexdigest()

def read_archives(root):
    root=Path(root)
    files=sorted((root/"by-mac").glob("*/reference-3d.json"))
    if not files:
        raise ValueError("No reference-3d.json files found in import/by-mac")
    result={}
    for path in files:
        if path.is_symlink() or path.parent.is_symlink() or path.stat().st_size>8*1024*1024:
            raise ValueError("Unsafe or oversized archive: "+path.parent.name)
        doc=json.loads(path.read_text(encoding="utf-8"))
        mac=doc.get("mac")
        count=doc.get("led_count")
        layout=doc.get("layout")
        if doc.get("schema")!=1 or not isinstance(mac,str) or not re.fullmatch("[a-f0-9]{12}",mac) or mac!=path.parent.name:
            raise ValueError("Archive identity mismatch: "+path.parent.name)
        if type(count) is not int or not 0<count<=100000 or not isinstance(layout,dict) or layout.get("source")!="3d" or type(layout.get("synthesized")) is not bool:
            raise ValueError("Invalid 3D reference: "+mac)
        coords=layout.get("coordinates")
        if not isinstance(coords,list) or len(coords)!=count or any(not isinstance(c,dict) or any(type(c.get(a)) not in (int,float) or not math.isfinite(c[a]) for a in ("x","y","z")) for c in coords):
            raise ValueError("Invalid LED coordinates: "+mac)
        if doc.get("sha256")!=digest(layout):
            raise ValueError("Archive checksum mismatch: "+mac)
        result[mac]={"name":str(doc.get("name_at_archive",mac)), "led_count":count,
            "layout":layout,"sha256":digest(layout),"source":"imported-cli-3d",
            "archive_provenance":str(doc.get("provenance","unknown")),
            "archive_created_utc":str(doc.get("created_utc","unknown"))}
    return result

def merge_archives(saved, incoming, identities, *, overwrite=False, allow_unverified=False):
    """Keep legacy defaults; explicit options permit offline imports and replacements."""
    merged=dict(saved)
    added=[]
    unchanged=[]
    skipped=[]
    for mac, record in incoming.items():
        name=record["name"]
        if mac not in identities and not allow_unverified:
            skipped.append(name+": not verified among configured controllers")
            continue
        if mac in identities and identities[mac]!=record["led_count"]:
            skipped.append(name+": live LED count differs")
            continue
        if mac in merged:
            old=merged[mac]
            if old.get("led_count")!=record["led_count"] or digest(old["layout"])!=record["sha256"]:
                if not overwrite:
                    skipped.append(name+": conflicting existing reference preserved")
                    continue
                merged[mac]=record
                added.append(name)
            else:
                unchanged.append(name)
            continue
        merged[mac]=record
        added.append(name)
    return merged,added,unchanged,skipped
