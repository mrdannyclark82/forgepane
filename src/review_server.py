"""Loopback-only review server. Share access through an authenticated SSH tunnel."""
import argparse
from contextlib import closing
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import sqlite3


class Handler(BaseHTTPRequestHandler):
    def reply(self, status, value):
        body = json.dumps(value).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        try:
            size = int(self.headers.get("Content-Length", "0"))
            if self.headers.get_content_type() != "application/json" or not 0 < size <= 16000:
                raise ValueError()
            item = json.loads(self.rfile.read(size))
            key = item["key"]
            if not isinstance(key, str) or not 0 < len(key) <= 1024:
                raise ValueError()
            with closing(sqlite3.connect(self.server.database)) as db, db:
                if self.path == "/comment":
                    author, note, line = item["author"], item["note"], item["line"]
                    if not (isinstance(author, str) and 0 < len(author) <= 80
                            and isinstance(note, str) and 0 < len(note) <= 3000
                            and type(line) is int and line > 0):
                        raise ValueError()
                    db.execute("INSERT INTO comments (document, author, line, note) VALUES (?, ?, ?, ?)",
                               (key, author, line, note))
                elif self.path != "/comments":
                    self.reply(404, {"error": "Unknown endpoint"})
                    return
                rows = db.execute(
                    "SELECT author, line, note FROM comments WHERE document=? ORDER BY id DESC LIMIT 100",
                    (key,)).fetchall()
            self.reply(200, list(reversed(rows)))
        except (ValueError, KeyError, TypeError):
            self.reply(400, {"error": "Invalid review request"})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--db", type=Path, required=True)
    args = parser.parse_args()
    with closing(sqlite3.connect(args.db)) as db, db:
        db.execute("CREATE TABLE IF NOT EXISTS comments (id INTEGER PRIMARY KEY, document TEXT,"
                   " author TEXT, line INTEGER, note TEXT)")
    with ThreadingHTTPServer(("127.0.0.1", args.port), Handler) as server:
        server.database = args.db
        print(f"Review server at 127.0.0.1:{args.port}", flush=True)
        server.serve_forever()


if __name__ == "__main__":
    main()
