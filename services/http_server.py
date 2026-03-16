# -*- coding: utf-8 -*-

from http.server import HTTPServer, BaseHTTPRequestHandler

from .. import i18n
from ..core.config import CONFIG

def run_server(server_class=HTTPServer, handler_class=BaseHTTPRequestHandler, default_port=8080):
    port = CONFIG.get('server', {}).get('port', default_port)
    
    try:
        server_address = ('', port)
        httpd = server_class(server_address, handler_class)
        
        print(i18n._("🚀 Server started on http://0.0.0.0:{port}...").format(port=port))
        
        httpd.serve_forever()
        
    except OSError as e:
        print(i18n._("❌ Failed to start HTTP server, port {port} may already be in use. Error: {error}").format(port=port, error=e))
        exit(1)
    except Exception as e:
        print(i18n._("❌ An unknown error occurred while starting the HTTP server: {error}").format(error=e))
        exit(1)