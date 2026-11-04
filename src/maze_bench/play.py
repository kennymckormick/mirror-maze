"""Serve the packaged, model-free playable environment over local HTTP."""
import argparse
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import webbrowser
from mirror_maze import _assets


def create_server(host='127.0.0.1', port=8876):
    handler = partial(SimpleHTTPRequestHandler, directory=str(_assets()))
    return ThreadingHTTPServer((host, port), handler)


def main(argv=None):
    parser = argparse.ArgumentParser(description='Play Mirror Maze locally; no API key is needed')
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--port', type=int, default=8876)
    parser.add_argument('--open', action='store_true', help='Open the local URL in the default browser')
    args = parser.parse_args(argv)
    with create_server(args.host, args.port) as server:
        host = '127.0.0.1' if args.host == '0.0.0.0' else args.host
        url = f'http://{host}:{server.server_port}/'
        print('Mirror Maze: ' + url, flush=True)
        print('Press Ctrl+C to stop.', flush=True)
        if args.open:
            webbrowser.open(url)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass


if __name__ == '__main__':
    main()
