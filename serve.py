#!/usr/bin/env python3
"""Preview the site on your computer: python3 serve.py, then open http://localhost:8080"""
import http.server
import os
import subprocess
import sys

here = os.path.dirname(os.path.abspath(__file__))
subprocess.run([sys.executable, os.path.join(here, "build.py")], check=True)
os.chdir(os.path.join(here, "_site"))
print("Serving at http://localhost:8080  (Ctrl+C to stop)")
http.server.ThreadingHTTPServer(("", 8080), http.server.SimpleHTTPRequestHandler).serve_forever()
