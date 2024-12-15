from flask import Flask, request, jsonify
from flask_cors import CORS
import osmnx as ox
import networkx as nx
import folium
from functools import lru_cache
import pickle
import os
from datetime import datetime
import threading
import time

app = Flask(__name__)
CORS(app)


class RouteService:
    def __init__(self):
        # CACHE_DIR = os.environ.get("CACHE_DIR", ".")
        # self.cache_file = os.path.join(CACHE_DIR, "bengaluru_graph.pickle")
        self.graph = None
        self.last_update = None
        self.graph_lock = threading.Lock()
        self.cache_file = "bengaluru_graph.pickle"
        self.update_interval = 24 * 60 * 60
        self.load_graph()

    def load_graph(self):
        """Load graph from cache or download if needed"""
        with self.graph_lock:
            if self.should_update_graph():
                try:
                    print("Loading graph from cache or downloading...")
                    if os.path.exists(self.cache_file):
                        with open(self.cache_file, "rb") as f:
                            self.graph = pickle.load(f)
                            print("Graph loaded from cache")
                    else:
                        self.download_and_cache_graph()
                    self.last_update = datetime.now()
                except Exception as e:
                    print(f"Error loading graph: {e}")
                    if not self.graph:
                        self.download_and_cache_graph()

    def should_update_graph(self):
        """Check if graph needs updating"""
        if not self.graph or not self.last_update:
            return True
        time_diff = (datetime.now() - self.last_update).total_seconds()
        return time_diff > self.update_interval

    def download_and_cache_graph(self):
        """Download and cache the graph"""
        print("Downloading new graph...")
        try:
            # Set up the place query
            place_query = {
                "city": "Bengaluru",
                "state": "Karnataka",
                "country": "India",
            }

            # Download the graph
            self.graph = ox.graph.graph_from_place(
                place_query, network_type="drive", simplify=True
            )

            # Add speed and travel time information
            # Default speed of 30 km/h for edges without speed data
            for _, _, data in self.graph.edges(data=True):
                if "maxspeed" not in data:
                    data["maxspeed"] = 30
                if isinstance(data["maxspeed"], list):
                    data["maxspeed"] = float(data["maxspeed"][0])
                if isinstance(data["maxspeed"], str):
                    data["maxspeed"] = float(data["maxspeed"].split()[0])

                # Calculate travel time in seconds
                length = float(data["length"])  # length in meters
                speed = float(data["maxspeed"])  # speed in km/h
                # Convert speed to m/s and calculate travel time
                data["travel_time"] = length / (speed * 1000 / 3600)

            # Cache the graph
            with open(self.cache_file, "wb") as f:
                pickle.dump(self.graph, f)
            print("Graph downloaded and cached successfully")
        except Exception as e:
            print(f"Error downloading graph: {e}")
            # Fallback to bounding box method if place name fails
            self.download_using_bbox()

    def download_using_bbox(self):
        """Fallback method using bounding box for Bengaluru"""
        print("Attempting to download using bounding box...")
        try:
            # Bengaluru approximate bounding box
            north, south = 13.023, 12.864
            east, west = 77.766, 77.484

            self.graph = ox.graph.graph_from_bbox(
                north=north,
                south=south,
                east=east,
                west=west,
                network_type="drive",
                simplify=True,
            )

            # Add speed and travel time information
            for _, _, data in self.graph.edges(data=True):
                if "maxspeed" not in data:
                    data["maxspeed"] = 30
                if isinstance(data["maxspeed"], list):
                    data["maxspeed"] = float(data["maxspeed"][0])
                if isinstance(data["maxspeed"], str):
                    data["maxspeed"] = float(data["maxspeed"].split()[0])

                # Calculate travel time in seconds
                length = float(data["length"])
                speed = float(data["maxspeed"])
                data["travel_time"] = length / (speed * 1000 / 3600)

            with open(self.cache_file, "wb") as f:
                pickle.dump(self.graph, f)
            print("Graph downloaded using bounding box and cached")
        except Exception as e:
            print(f"Error in bbox download: {e}")
            raise

    @lru_cache(maxsize=1000)
    def get_nearest_node(self, lat, lng):
        """Cache nearest node lookups"""
        return ox.nearest_nodes(self.graph, lng, lat)

    def find_shortest_path(self, origin, destination):
        """Find the shortest path between two points"""
        try:
            origin_node = self.get_nearest_node(*origin)
            dest_node = self.get_nearest_node(*destination)

            path = nx.shortest_path(
                self.graph, origin_node, dest_node, weight="travel_time"
            )
            return path
        except (nx.NetworkXNoPath, nx.NodeNotFound) as e:
            print(f"Path finding error: {e}")
            return None

    def create_route_map(self, origin, path):
        """Create a Folium map for the route"""
        if not path:
            return None, None

        try:
            path_coords = [
                (self.graph.nodes[node]["y"], self.graph.nodes[node]["x"])
                for node in path
            ]

            m = folium.Map(location=[origin[0], origin[1]], zoom_start=13)

            # Add markers
            folium.Marker(
                path_coords[0], popup="Start", icon=folium.Icon(color="green")
            ).add_to(m)

            folium.Marker(
                path_coords[-1], popup="End", icon=folium.Icon(color="red")
            ).add_to(m)

            # Add route line
            folium.PolyLine(path_coords, color="blue", weight=4, opacity=0.8).add_to(m)

            return m._repr_html_(), path_coords
        except Exception as e:
            print(f"Map creation error: {e}")
            return None, None


# Initialize the route service
route_service = RouteService()


@app.route("/shortest-path", methods=["POST"])
def shortest_path():
    try:
        data = request.json
        origin = tuple(data["origin"])
        destination = tuple(data["destination"])

        # Find the shortest path
        path = route_service.find_shortest_path(origin, destination)

        if not path:
            return jsonify({"error": "No path found between the given points"}), 404

        # Create the map
        map_html, path_coords = route_service.create_route_map(origin, path)

        if not map_html:
            return jsonify({"error": "Error creating map visualization"}), 500

        # Calculate route statistics
        route_stats = {
            "total_distance": sum(
                float(route_service.graph[path[i]][path[i + 1]][0]["length"])
                for i in range(len(path) - 1)
            ),
            "estimated_time": sum(
                float(route_service.graph[path[i]][path[i + 1]][0]["travel_time"])
                for i in range(len(path) - 1)
            ),
        }

        return jsonify({"path": path_coords, "stats": route_stats})

    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/", defaults={"path": ""})
@app.route("/<path:path>")
def all_routes(path):
    return f"""
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Python Server</title>
        <style>
            body {{
                font-family: Arial, sans-serif;
                text-align: center;
                padding: 50px;
            }}
            h1 {{
                color: #4CAF50;
            }}
        </style>
    </head>
    <body>
        <h1>This is a Python Server</h1>
        <p>The server is running and healthy.</p>
        <p>You accessed: <strong>{path}</strong></p>
    </body>
    </html>
    """


# Background task to periodically update the graph
def periodic_graph_update():
    while True:
        time.sleep(route_service.update_interval)
        route_service.load_graph()


# Start the background update task
update_thread = threading.Thread(target=periodic_graph_update, daemon=True)
update_thread.start()

if __name__ == "__main__":
    # app.run(debug=True, threaded=True)
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
