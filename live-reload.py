import sys
import time
from watchdog.observers import Observer
from watchdog.events import PatternMatchingEventHandler
from contextlib import suppress
import http.server
import socketserver
import threading
import asyncio
import websockets
import os 
import io

ACTIVE_WS_CLIENTS = set()
INJECTED_CODE = """
<script>
    document.addEventListener("DOMContentLoaded", function() {
        const socket = new WebSocket("ws://localhost:8001/devsocket");

        socket.addEventListener("open", (event) => {
            console.log("Connected to devsocket, hello world and good luck with your new project");
        });

        socket.addEventListener("message", (event) => {
            console.log("Message from server:", event.data);
            if (event.data === "reload") {
                location.reload();
            }
        });

        socket.addEventListener("close", (event) => {
            if (event.wasClean) {
                console.log(`Closed cleanly, code=${event.code}, reason=${event.reason}`);
            } else {
                // For example, server process killed or network down
                console.error("Connection died");
            }
        });

        socket.addEventListener("error", (error) => {
            console.error(`WebSocket Error: ${error.message}`);
        });
    });
</script>
"""

class MyHandler(PatternMatchingEventHandler):
    patterns = ["*.html", "*.css", "*.js"]
    debounce_time = 0.5  # 0.5 seconds debounce time, to not reload too fast
    last_modified_time = {}

    def process(self, event):
        current_time = time.time()
        if event.src_path in self.last_modified_time:
            time_since_last_modified = current_time - self.last_modified_time[event.src_path]
            if time_since_last_modified <= self.debounce_time:
                return  # Ignore this event, as it's too close to the previous one
        self.last_modified_time[event.src_path] = current_time
        print(f'{event.src_path} has been {event.event_type}')
        
        # Notify all WebSocket clients to reload
        asyncio.run(broadcast("reload"))

        self.last_modified_time[event.src_path] = current_time
        print(f'{event.src_path} has been {event.event_type}')

    def on_modified(self, event):
        self.process(event)

    def on_created(self, event):
        self.process(event)

def watch_files():
    path = "."  # current directory
    observer = Observer()
    observer.schedule(MyHandler(), path=path, recursive=False)
    observer.start()

    print("File watcher started.")
    try:
        while True:
            time.sleep(1)
    except:
        observer.stop()

    observer.join()

class CustomHTTPRequestHandler(http.server.SimpleHTTPRequestHandler):

    def send_head(self):
        """Override to inspect and modify HTML content."""
        # Get the original response
        response = super().send_head()

        if self.path.endswith("/") or self.path.endswith(".html"):
            file_path = os.path.join(os.getcwd(), self.path.lstrip("/"))
            
            # Check if the path corresponds to a directory (serving index.html by default)
            if os.path.isdir(file_path):
                file_path = os.path.join(file_path, "index.html")
            
            # Check if the file is an HTML file
            if os.path.isfile(file_path):
                with open(file_path, 'r', encoding='utf-8') as f:
                    content = f.read()
                    print("HTML file loaded:", self.path)

                    # Modify the content
                    content = content.replace("</body>", f"{INJECTED_CODE}</body>")

                    # Courtesy to the user
                    content = content +  "<!-- Running in dev mode with Python live reload!!! -->" 

                # Encode the modified content and set it as the response
                # Who cares about headers anyway
                encoded_content = content.encode('utf-8')
                return io.BytesIO(encoded_content)
        
        return response


def start_http_server():
    PORT = 8000
    Handler = CustomHTTPRequestHandler
    with socketserver.TCPServer(("", PORT), Handler) as httpd:
        print(f"Server started at http://localhost:{PORT}")
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("HTTP server shutting down...")
            httpd.server_close()
            httpd.shutdown()


async def devsocket(websocket, path):
    ACTIVE_WS_CLIENTS.add(websocket)
    try:
        async for message in websocket:
            # For this example, we're not expecting any message from the client
            pass
    finally:
        # Remove the WebSocket client from the set when it disconnects
        ACTIVE_WS_CLIENTS.remove(websocket)


def start_websocket_server():
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        start_server = websockets.serve(devsocket, 'localhost', 8001)
        asyncio.get_event_loop().run_until_complete(start_server)
        asyncio.get_event_loop().run_forever()
    except KeyboardInterrupt:
        print("WebSocket server shutting down...")
        tasks = [task for task in asyncio.all_tasks() if task is not asyncio.current_task()]
        
        # Cancel tasks and wait for them to finish
        [task.cancel() for task in tasks]

        loop.run_until_complete(asyncio.gather(*tasks, return_exceptions=True))
        loop.stop()

async def broadcast(message):
    """Send a message to all active WebSocket clients."""
    if ACTIVE_WS_CLIENTS:
        tasks = [asyncio.create_task(ws.send(message)) for ws in ACTIVE_WS_CLIENTS]
        await asyncio.wait(tasks)

if __name__ == "__main__":
    thread = threading.Thread(target=watch_files)
    thread.start()

    ws_thread = threading.Thread(target=start_websocket_server)
    ws_thread.start()
    
    try:
        # Start the HTTP server
        start_http_server()
    except KeyboardInterrupt:
        print("Main thread caught KeyboardInterrupt. Exiting...")
