# app.py
from flask import Flask, render_template, request, jsonify, redirect, url_for
from flask_socketio import SocketIO
import a2s
import json
import threading
import time
import os
from datetime import datetime
import secrets


app = Flask(__name__)
# Try to get from environment variables, or generate a random one
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY") or secrets.token_hex(16)
socketio = SocketIO(app)

# Store for servers
SERVER_FILE = "servers.json"
SERVERS = {}

def load_servers():
    """Load servers from JSON file"""
    global SERVERS
    if os.path.exists(SERVER_FILE):
        with open(SERVER_FILE, "r") as f:
            SERVERS = json.load(f)
    else:
        SERVERS = {}
        save_servers()

def save_servers():
    """Save servers to JSON file"""
    with open(SERVER_FILE, "w") as f:
        json.dump(SERVERS, f)

def query_server(server_id, address, port):
    """Query a Source server for info and players"""
    try:
        # Split into parts
        server_info = {}
        
        # Query server info
        info = a2s.info((address, port))
        
        # Create a dictionary with safe property access
        server_info["info"] = {}
        
        # Handle different property naming conventions and safely access attributes
        for attr_name, possible_names in {
            "name": ["server_name", "name"],
            "map": ["map_name", "map"],
            "player_count": ["player_count", "players"],
            "max_players": ["max_players", "max_players"],
            "game": ["game", "game_id"],
            "vac_enabled": ["vac_enabled", "vac_secured"],
            "server_type": ["server_type", "type"],
            "os": ["platform", "os", "environment"],
            "version": ["version"],
            "ping": ["ping"]
        }.items():
            # Try each possible attribute name
            for possible_name in possible_names:
                if hasattr(info, possible_name):
                    server_info["info"][attr_name] = getattr(info, possible_name)
                    break
            # If none found, set to None
            if attr_name not in server_info["info"]:
                server_info["info"][attr_name] = None
        
        # Ensure all expected fields are present
        expected_fields = ["name", "map", "player_count", "max_players", "game", 
                          "vac_enabled", "server_type", "os", "version", "ping"]
        for field in expected_fields:
            if field not in server_info["info"]:
                server_info["info"][field] = None
        
        # Query players
        try:
            players = a2s.players((address, port))
            server_info["players"] = []
            
            for player in players:
                player_data = {}
                
                # Safely access player properties
                if hasattr(player, 'name'):
                    player_data["name"] = player.name if player.name else "Unknown"
                else:
                    player_data["name"] = "Unknown"
                    
                if hasattr(player, 'score'):
                    player_data["score"] = player.score
                else:
                    player_data["score"] = 0
                    
                if hasattr(player, 'duration'):
                    player_data["duration"] = player.duration
                else:
                    player_data["duration"] = 0
                
                server_info["players"].append(player_data)
                
        except Exception as e:
            server_info["players"] = []
            server_info["player_error"] = str(e)
            
        # Query rules
        try:
            rules = a2s.rules((address, port))
            
            # Handle rules as either a dictionary or an object with attributes
            if isinstance(rules, dict):
                server_info["rules"] = {k: v for k, v in rules.items() if k != ""}
            else:
                # Try to convert to dict if it's an object
                try:
                    rules_dict = {k: getattr(rules, k) for k in dir(rules) 
                                 if not k.startswith('_') and not callable(getattr(rules, k))}
                    server_info["rules"] = rules_dict
                except:
                    server_info["rules"] = {}
        except Exception as e:
            server_info["rules"] = {}
            server_info["rules_error"] = str(e)
        
        server_info["status"] = "online"
        server_info["last_updated"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        
    except Exception as e:
        server_info = {
            "status": "offline",
            "error": str(e),
            "last_updated": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        }
    
    # Emit the update to clients
    socketio.emit('server_update', {'id': server_id, 'data': server_info})
    return server_info

def query_all_servers():
    """Query all servers in a loop"""
    while True:
        for server_id, server in SERVERS.items():
            try:
                SERVERS[server_id]["data"] = query_server(
                    server_id, 
                    server["address"], 
                    server["port"]
                )
            except Exception as e:
                print(f"Error querying server {server_id}: {e}")
        
        save_servers()
        time.sleep(30)  # Update every 30 seconds

@app.route('/')
def index():
    """Render the main dashboard"""
    return render_template('index.html', servers=SERVERS)

@app.route('/add_server', methods=['POST'])
def add_server():
    """Add a new server to monitor"""
    name = request.form.get('name')
    address = request.form.get('address')
    port = int(request.form.get('port', 27015))
    
    # Generate a unique ID for the server
    server_id = f"{len(SERVERS) + 1}_{int(time.time())}"
    
    # Add server to the list
    SERVERS[server_id] = {
        "name": name,
        "address": address,
        "port": port,
        "added_on": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "data": {}
    }
    
    # Initial query to populate data
    try:
        SERVERS[server_id]["data"] = query_server(server_id, address, port)
    except Exception as e:
        SERVERS[server_id]["data"] = {"status": "error", "error": str(e)}
    
    save_servers()
    return redirect(url_for('index'))

@app.route('/remove_server/<server_id>', methods=['POST'])
def remove_server(server_id):
    """Remove a server from monitoring"""
    if server_id in SERVERS:
        del SERVERS[server_id]
        save_servers()
    return redirect(url_for('index'))

@app.route('/refresh_server/<server_id>', methods=['POST'])
def refresh_server(server_id):
    """Manually refresh a specific server"""
    if server_id in SERVERS:
        server = SERVERS[server_id]
        try:
            SERVERS[server_id]["data"] = query_server(
                server_id, 
                server["address"], 
                server["port"]
            )
            save_servers()
        except Exception as e:
            return jsonify({"status": "error", "message": str(e)}), 500
    return jsonify({"status": "success"})

@socketio.on('connect')
def on_connect():
    """Handle client connection"""
    print('Client connected')

@socketio.on('disconnect')
def on_disconnect():
    """Handle client disconnection"""
    print('Client disconnected')

if __name__ == '__main__':
    # Load existing servers
    load_servers()
    
    # Start the background thread for querying servers
    thread = threading.Thread(target=query_all_servers)
    thread.daemon = True
    thread.start()
    
    # Run the app
    socketio.run(app, debug=True, host='0.0.0.0', port=4000)