"""Bounded independent controller reads; never use for dependent writes."""
from concurrent.futures import ThreadPoolExecutor

def add_workers(parser):
    parser.add_argument("--workers", type=int, choices=range(1, 7), default=3,
                        help="Independent controller reads (default 3; 1 disables concurrency)")

def controller_reads(devices, read, workers=3):
    """Yield (device, result, exception) in input order; drain each wave."""
    if not 1 <= workers <= 6:
        raise ValueError("workers must be 1-6")
    remaining = list(devices)
    def safe(device):
        try:
            return device, read(device), None
        except Exception as exc:
            return device, None, exc
    while remaining:
        batch = remaining[:workers]
        del remaining[:workers]
        if len(batch) == 1:
            results = [safe(batch[0])]
        else:
            with ThreadPoolExecutor(max_workers=len(batch)) as pool:
                results = list(pool.map(safe, batch))
        if any(error is not None for _, _, error in results):
            workers = 1
        yield from results
