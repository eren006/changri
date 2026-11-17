"""Local studio server: serves this folder and saves POSTed data-URL images into ./out."""
import base64, http.server, os, re, sys

ROOT = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(ROOT, "out")
os.makedirs(OUT, exist_ok=True)


class H(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *a, **k):
        super().__init__(*a, directory=ROOT, **k)

    def do_POST(self):
        name = self.path.split("name=", 1)[-1]
        if not re.fullmatch(r"[a-z0-9_-]+\.(png|webp)", name):
            self.send_response(400); self.end_headers(); return
        body = self.rfile.read(int(self.headers["Content-Length"])).decode()
        data = base64.b64decode(body.split(",", 1)[1])
        with open(os.path.join(OUT, name), "wb") as f:
            f.write(data)
        self.send_response(200); self.end_headers(); self.wfile.write(str(len(data)).encode())

    def log_message(self, *a):
        pass


http.server.ThreadingHTTPServer(("127.0.0.1", int(sys.argv[1]) if len(sys.argv) > 1 else 8770), H).serve_forever()
