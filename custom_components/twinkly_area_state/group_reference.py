"""Match an explicitly reviewed group-map candidate to freshly read LAN geometry."""

def match_candidate(candidate, records, geometry_matches):
    def identity(m):
        return (str(m["mac"]).replace(":","").replace("-","").lower(),
                m["led_count"],m["offset"],m["size"],m["role"])
    def index(members):
        result={}
        for member in members:
            key=identity(member)
            if key in result:
                raise ValueError("Duplicate group member")
            result[key]=member
        return result
    desired=index(candidate["members"])
    if not desired or candidate["led_count"]!=sum(m["led_count"] for m in desired.values()):
        raise ValueError("Candidate LED count mismatch")
    matches=[]
    for uid,record in records.items():
        actual=index(record["members"])
        if set(actual)!=set(desired) or record["led_count"]!=candidate["led_count"]:
            continue
        if not all(geometry_matches(actual[key]["layout"],desired[key]["layout"]) for key in actual):
            raise ValueError("Live group geometry differs from reviewed candidate")
        matches.append((uid,record))
    if len(matches)!=1:
        raise ValueError("Expected exactly one matching live group")
    return matches[0]

def replace_reference(archives, uid, record, stamp):
    groups=archives.setdefault("groups",{})
    previous=groups.get(uid)
    if previous is not None and previous!=record:
        if {(m["mac"],m["led_count"],m["offset"],m["role"]) for m in previous["members"]} != {(m["mac"],m["led_count"],m["offset"],m["role"]) for m in record["members"]}:
            raise ValueError("Existing reference topology differs")
        archives.setdefault("group_replacement_backups",{}).setdefault(stamp,{})[uid]=previous
    groups[uid]=record
