import http.client
import json
import sys
import tempfile
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import app

td = tempfile.TemporaryDirectory()
app.DATA_DIR = Path(td.name) / "data"
app.DATABASE_PATH = app.DATA_DIR / "supertory.sqlite3"
app.initialise_database()
server = app.ThreadingHTTPServer(("127.0.0.1", 0), app.SuperToryHandler)
threading.Thread(target=server.serve_forever, daemon=True).start()
port = int(server.server_port)


def req(m, p, b=None):
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=30)
    payload = None
    h = {}
    if b is not None:
        payload = json.dumps(b, ensure_ascii=False).encode()
        h["Content-Type"] = "application/json"
    c.request(m, p, payload, h)
    r = c.getresponse()
    d = r.read()
    st = r.status
    c.close()
    try:
        return st, json.loads(d)
    except Exception:
        return st, d


st, p = req("POST", "/api/projects", {"title": "e", "main_genre": "판타지"})
pid = p["id"]
st, p2 = req("POST", "/api/projects", {"title": "e2", "main_genre": "로맨스"})
st, r = req("PUT", "/api/projects/reorder", {"project_ids": [p2["id"], pid]})
print("reorder PUT", st, "ok" if st == 200 else r)
st, lm = req("PUT", "/api/projects/list-mode", {"mode": "recent"})
print("list-mode PUT", st, "ok" if st == 200 else lm)
st, ch = req("POST", f"/api/projects/{pid}/chapters", {"title": "1장"})
st, rn = req("PUT", f"/api/projects/{pid}/chapters/renumber-titles", {"style": "jang"})
print("renumber PUT", st, rn)
st, part = req("POST", f"/api/projects/{pid}/parts", {"title": "1부"})
st, pr = req("PUT", f"/api/projects/{pid}/parts/reorder", {"part_ids": [part["id"]]})
print("parts reorder PUT", st)
st, sc = req("POST", f"/api/chapters/{ch['id']}/scenes", {"title": "1화"})
st, su = req(
    "PUT",
    f"/api/scenes/{sc['id']}/summary",
    {"summary": {"plot": "요약", "characters": [], "events": []}},
)
print("summary PUT", st, str(su)[:120])
# empty project renumber (no chapters) - FE guards this
st, p3 = req("POST", "/api/projects", {"title": "empty", "main_genre": "판타지"})
st, rn0 = req("PUT", f"/api/projects/{p3['id']}/chapters/renumber-titles", {"style": "jang"})
print("renumber no chapters", st, rn0)
server.shutdown()
td.cleanup()
