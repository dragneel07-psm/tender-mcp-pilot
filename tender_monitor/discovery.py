"""Source discovery: bulk import from the official Ministry (MOFAGA) local-government directory."""
import urllib.error
import urllib.parse

from . import net, storage
from .config import PROVINCES, SOURCES
from .parsing import OfficialDirectoryParser


def official_directory_sources(province_code):
    if province_code not in PROVINCES: raise ValueError("Province must be a code from 1 to 7.")
    province=PROVINCES[province_code]
    pages=[]
    for kind in ("mun", "village-mun"):
        base=f"https://mofaga.gov.np/local-contact/{kind}-prov-{province_code}"
        pages.append(base)
        pages.extend(f"{base}?page={page}" for page in range(1,6))
    found=[]; errors=[]
    for page in pages:
        try:
            parser=OfficialDirectoryParser(); parser.feed(net.fetch(page))
        except (urllib.error.URLError, TimeoutError, ValueError) as exc:
            errors.append({"page":page,"detail":str(exc)})
            continue
        for cells, links in parser.rows:
            website=next((urllib.parse.urljoin(page, link) for link in links if ".gov.np" in link), None)
            if not website or len(cells) < 4: continue
            name=cells[2]
            if not name or "स्थानीय तहको नाम" in name: continue
            found.append({"id":storage.source_id(name),"name":name,"url":website,"notice_url":website,"keywords":["tender","bid","bolpatra","बोलपत्र","दरभाउ","खरिद"],"province":province})
    return found, errors


def tag_existing_sources(items):
    changed=False
    for item in items:
        if "province" not in item:
            item["province"]="National / other" if "jobsnepal" in storage.normalized_host(item["url"]) else "Sudurpashchim"; changed=True
    return changed


def bootstrap_province(province_code):
    # The network fetch (up to 12 MOFAGA directory pages) happens before the lock is taken, so a
    # slow bootstrap import doesn't hold sources.json/watchlists.json reserved -- and hold up an
    # admin adding/editing a source through the dashboard in the meantime -- for that whole span.
    imported, errors=official_directory_sources(province_code)
    with storage.REGISTRY_WRITE_LOCK:
        existing=storage.sources(); by_host={storage.normalized_host(s["url"]):s for s in existing}; by_name={s["name"]:s for s in existing}
        changed=tag_existing_sources(existing)
        for source in imported:
            if storage.normalized_host(source["url"]) not in by_host and source["name"] not in by_name:
                existing.append(source); by_host[storage.normalized_host(source["url"])]=source; by_name[source["name"]]=source
                changed=True
        if changed: storage.save_sources(existing)
        total=len(existing)
    return {"province":PROVINCES[province_code],"sources":total,"imported":len(imported),"directory_errors":errors,"file":str(SOURCES)}


def bootstrap_sudurpashchim(): return bootstrap_province("7")
def bootstrap_karnali(): return bootstrap_province("6")
def bootstrap_lumbini(): return bootstrap_province("5")


def sync_all_local_levels():
    results=[]
    for province_code in PROVINCES:
        try: results.append(bootstrap_province(province_code))
        except (urllib.error.URLError, TimeoutError, ValueError) as exc:
            results.append({"province":PROVINCES[province_code],"error":str(exc)})
    return {"results":results,"sources":len(storage.sources()),"file":str(SOURCES)}
