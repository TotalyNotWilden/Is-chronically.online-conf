from flask import Flask, jsonify, request, redirect, render_template
from dotenv import load_dotenv
from cloudflare import Cloudflare
import requests
import os
import json
import signal
import sys
import subprocess
import threading
import time

def save_sites_and_exit(signal_received, frame):
    print("\nShutting down...")
    # Reconstruct the original format including URL types
    sites_data = {}
    for name, target in sites.items():
        record = dns_records.get(name, [None, None, None, True])
        # Check if this should be saved as URL type (redirect URL stored in sites)
        if record[0] == self_ip and target.startswith(('http://', 'https://')):
            sites_data[name] = [target, record[1], "URL", record[3]]
        else:
            sites_data[name] = [target, record[1], record[2], record[3]]
    
    with open('sites.json', 'w') as f:
        json.dump(sites_data, f, indent=4)
    print("Saved sites.json")
    sys.exit(0)

signal.signal(signal.SIGINT, save_sites_and_exit)  # Ctrl+C
signal.signal(signal.SIGTERM, save_sites_and_exit) # kill command

def git_pull_loop():
    while True:
        time.sleep(600)  # 10 minutes
        try:
            print("Running git pull...")
            result = subprocess.run(['git', 'pull'], capture_output=True, text=True, cwd=os.path.dirname(__file__))
            print(f"Git pull output: {result.stdout}")

            print("Reloading sites.json...")
            parse_sites()
            print("Reloaded sites.json after git pull")

            if result.stderr:
                print(f"Git pull errors: {result.stderr}")
        except Exception as e:
            print(f"Error running git pull: {e}")


self_ip = requests.get('https://ipinfo.io/').json()['ip']

load_dotenv()

dns_records = {}
sites = {}

app = Flask(__name__)
app.config['SERVER_NAME'] = 'is-chronically.online'
client = Cloudflare(
    api_token=os.getenv("CLOUDFLARE_API_TOKEN"),
)

def parse_sites():
    global sites
    global dns_records
    with open('sites.json', 'r') as f:
        data = json.load(f)
        for name, value in data.items():
            # Handle both [target, record_id, record_type] and just target string
            if isinstance(value, list):
                target = value[0]
                record_id = value[1] if len(value) > 1 else ""
                record_type = value[2] if len(value) > 2 else "A"
                proxied = value[3] if len(value) > 3 else True
            else:
                target = value
                record_id = ""
                record_type = "A"
                proxied = True
            
            # If type is URL, store the redirect URL but use self_ip for DNS
            if record_type == "URL":
                sites[name] = target  # Keep the redirect URL
                dns_records[name] = [self_ip, record_id, "A", proxied]  # Use self_ip for DNS A record
            else:
                sites[name] = target
                dns_records[name] = [target, record_id, record_type, proxied]

def get_dns_records(zone_id):
    global dns_records
    # zone = client.zones.get(zone_id)
    records = client.dns.records.list(zone_id=zone_id)
    remote_records = {}
    for record in records.result:
        remote_records[record.name] = [record.content, record.id, record.type]
    return remote_records
def add_dns_record(zone_id, record_type, name, content):
    return jsonify(client.dns.records.create(zone_id=zone_id, name=name, type=record_type, content=content, proxied=True))

def delete_dns_record(zone_id, name):
    return jsonify(client.dns.records.delete(zone_id=zone_id, dns_record_id=dns_records[name][1]))

def update_dns_record(zone_id, record_id, name, content):
    return jsonify(client.dns.records.update(zone_id=zone_id, dns_record_id=record_id, name=name, content=content, proxied=True, type="A"))

@app.route('/api/records')
def home():
    return jsonify(dns_records)

@app.route('/api/add_page')
def api_add_record():
    zone_id = os.getenv("CLOUDFLARE_ZONE_ID")
    record_type = "A"
    req_name = request.args.get('name')
    if not req_name:
        return jsonify({"error": "Missing 'name' parameter"}), 400
    name = req_name + ".is-chronically.online" if not req_name.endswith(".is-chronically.online") else req_name
    target = request.args.get('target')
    sites[name] = target

    content = self_ip
    print(f"Adding DNS record: {record_type} {name} -> {content}")
    return add_dns_record(zone_id, record_type, name, content)

@app.route("/api/reload_sites")
def reload_sites():
    global sites
    with open('sites.json', 'r') as f:
        data = json.load(f)
        for name, value in data.items():
            if isinstance(value, list):
                target = value[0]
            else:
                target = value
            sites[name] = target
    return jsonify({"status": "reloaded", "sites": sites})

@app.route('/api/delete_page')
def api_delete_record():
    zone_id = os.getenv("CLOUDFLARE_ZONE_ID")
    name = request.args.get('name')
    print(f"Deleting DNS record: {name}")
    return delete_dns_record(zone_id, name)


# Matrix
@app.route('/.well-known/matrix/server', methods=['GET'])
def matrix_well_known():
    return jsonify({
        "m.server": "matrix.is-chronically.online:443"
    })

@app.route('/.well-known/matrix/client', methods=['GET'])
def matrix_client_well_known():
    return jsonify({
        "m.homeserver": {
            "base_url": "https://matrix.is-chronically.online"
        }
    })

# Subdomains
@app.route('/', defaults={'path': ''})
@app.route('/<path:path>')
def subdomain(path):
    host = request.host
    sub = host.split('.')[0] if host != "is-chronically.online" else None
    
    print(sub)

    if sub == None:
        return render_template('index.html', sites=sites)

    found = False
    for s in sites:
        if s.split('.')[0] == sub:
            sub = s
            found = True
            break
    if not found:
        return "Subdomain not found", 404

    target_base = sites[sub]
    
    # If target is a full URL (starts with http:// or https://), redirect directly
    if target_base.startswith(('http://', 'https://')):
        if path:
            target_url = f"{target_base.rstrip('/')}/{path}"
        else:
            target_url = target_base
        return redirect(target_url, code=302)
    
    # Otherwise construct URL from base
    target_url = f"{target_base}/{path}"
    return redirect(target_url, code=302)

if __name__ == '__main__':
    zone_id = os.getenv("CLOUDFLARE_ZONE_ID")
    # Get remote records without overwriting local
    remote_dns_records = get_dns_records(zone_id)

    print(remote_dns_records)
    
    for name, content in sites.items():
        local_record = dns_records.get(name)
        remote_record = remote_dns_records.get(name)
        
        # For URL types, use self_ip as the DNS content
        dns_content = self_ip if local_record and local_record[2] == "A" and content.startswith(('http://', 'https://')) else (dns_records.get(name, [None])[0] if local_record else content)
        
        # Check if DNS ID is blank/None (manually added in JSON)
        if not local_record or not local_record[1]:
            if remote_record:
                print(f"\nConflict detected for {name}:")
                print(f"  Local:  {dns_content} {'(URL: ' + content + ')' if local_record and local_record[2] == 'A' and content.startswith(('http://', 'https://')) else ''}")
                print(f"  Remote: {remote_record[0]}")
                print("Attempting to use local value...")
                try:
                    # Try to create with local value
                    dns_type = "A"
                    result = client.dns.records.create(zone_id=zone_id, name=name, type=dns_type, content=dns_content, proxied=local_record[3] if local_record else True)
                    dns_records[name] = [dns_content, result.id, dns_type, local_record[3] if local_record else True]
                    print(f"✓ Successfully created with local value: {dns_content}")
                except Exception as e:
                    print(f"✗ Failed to create with local value: {e}")
                    print(f"Using remote value: {remote_record[0]}")
                    sites[name] = remote_record[0]
                    dns_records[name] = [remote_record[0], remote_record[1], remote_record[2], local_record[3] if local_record else True]
            else:
                print(f"Creating DNS record for {name} -> {dns_content}")
                # Use A record type for URL types
                dns_type = "A"
                result = client.dns.records.create(zone_id=zone_id, name=name, type=dns_type, content=dns_content, proxied=local_record[3] if local_record else True)
                dns_records[name] = [dns_content, result.id, dns_type, local_record[3] if local_record else True]
        elif remote_record and remote_record[0] != dns_content:
            print(f"\nConflict detected for {name}:")
            print(f"  Local:  {dns_content} {'(URL: ' + content + ')' if local_record and local_record[2] == 'A' and content.startswith(('http://', 'https://')) else ''}")
            print(f"  Remote: {remote_record[0]}")
            print("Attempting to use local value...")
            try:
                # Try to update with local value
                client.dns.records.update(
                    zone_id=zone_id,
                    dns_record_id=local_record[1],
                    name=name,
                    content=dns_content,
                    proxied=local_record[3],
                    type="A"
                )
                dns_records[name] = [dns_content, local_record[1], "A", local_record[3]]
                print(f"✓ Successfully updated to local value: {dns_content}")
            except Exception as e:
                print(f"✗ Failed to update with local value: {e}")
                print(f"Keeping remote value: {remote_record[0]}")
                sites[name] = remote_record[0]
                dns_records[name] = [remote_record[0], local_record[1], remote_record[2], local_record[3]]
    print(dns_records)
    
    # Start git pull thread
    # git_thread = threading.Thread(target=git_pull_loop, daemon=True)
    # git_thread.start()
    
    app.run(debug=True, port=5678, host="0.0.0.0")