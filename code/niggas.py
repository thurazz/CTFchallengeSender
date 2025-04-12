import requests
import re
import time
import json
import os
from threading import Thread, Lock
from queue import Queue
from datetime import datetime
from flask import Flask, request, jsonify, render_template, redirect, url_for
from collections import defaultdict, Counter

class CTFSubmitter:
    def __init__(self):
        # Configuration (TO BE MODIFIED TOMORROW)
        self.SERVER_URL = "http://10.10.0.1:8080/flags"  # Official endpoint
        self.TEAM_TOKEN = "7cdab75fd05396d3eaf14498b0726760"  # Replace with real token
        self.FLAG_REGEX = re.compile(r"^[A-Z0-9]{31}=$")  # Official regex
        
        # Thread-safe data structures
        self.flag_queue = Queue()
        self.lock = Lock()
        self.last_submission = 0
        self.rate_limit = 30  # Max 30 requests/minute
        
        # Stats tracking
        self.submission_history = []
        self.flags_by_team = defaultdict(int)
        self.flags_by_service = defaultdict(int)
        self.stats_file = "submission_stats.json"
        
        # Load previous stats if available
        self.load_stats()
        
        # Start threads
        Thread(target=self.submitter_thread, daemon=True).start()
        Thread(target=self.start_web_server, daemon=True).start()

    def load_stats(self):
        """Load previous statistics if available"""
        try:
            if os.path.exists(self.stats_file):
                with open(self.stats_file, 'r') as f:
                    data = json.load(f)
                    self.submission_history = data.get('history', [])
                    self.flags_by_team = defaultdict(int, data.get('by_team', {}))
                    self.flags_by_service = defaultdict(int, data.get('by_service', {}))
                print(f"[+] Loaded statistics from {self.stats_file}")
        except Exception as e:
            print(f"[-] Error loading stats: {str(e)}")

    def save_stats(self):
        """Save current statistics to file"""
        try:
            with open(self.stats_file, 'w') as f:
                json.dump({
                    'history': self.submission_history[-1000:],  # Keep last 1000 submissions
                    'by_team': dict(self.flags_by_team),
                    'by_service': dict(self.flags_by_service)
                }, f)
        except Exception as e:
            print(f"[-] Error saving stats: {str(e)}")

    def decode_flag(self, flag):
        """Decode flag information"""
        try:
            return {
                'round': int(flag[0:2], 36),
                'team': int(flag[2:4], 36),
                'service': int(flag[4:6], 36),
                'full_flag': flag
            }
        except:
            return None

    def validate_flag(self, flag):
        """Verify format and temporal validity"""
        if not self.FLAG_REGEX.match(flag):
            return False, "Invalid format"
        
        decoded = self.decode_flag(flag)
        if not decoded:
            return False, "Decoding failed"
        
        # Add round check here if needed
        return True, "Valid"

    def submit_flags(self, flags):
        """Submit a batch of flags to the server"""
        if not flags:
            return []

        try:
            response = requests.put(
                self.SERVER_URL,
                headers={'X-Team-Token': self.TEAM_TOKEN},
                json=flags,
                timeout=5
            )
            
            if response.status_code == 200:
                return response.json()
            else:
                print(f"HTTP Error {response.status_code}: {response.text}")
                return [{'flag': flag, 'status': 'ERROR', 'msg': f"HTTP {response.status_code}"} for flag in flags]
                
        except Exception as e:
            print(f"Connection error: {str(e)}")
            return [{'flag': flag, 'status': 'ERROR', 'msg': str(e)} for flag in flags]

    def submitter_thread(self):
        """Thread for rate-limited submission"""
        while True:
            now = time.time()
            elapsed = now - self.last_submission
            
            # Respect rate limiting (30/minute)
            if elapsed < 2.0:  # 60/30 = 2s between requests
                time.sleep(2.0 - elapsed)
            
            # Collect up to 100 flags per request (within 100KB)
            batch = []
            while len(batch) < 100 and not self.flag_queue.empty():
                flag = self.flag_queue.get()
                batch.append(flag)
            
            if batch:
                results = self.submit_flags(batch)
                self.last_submission = time.time()
                
                with self.lock:
                    for i, result in enumerate(results):
                        flag = batch[i] if i < len(batch) else "unknown"
                        status = result.get('status', 'ERROR')
                        msg = result.get('msg', 'No message')
                        
                        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                        decoded = self.decode_flag(flag)
                        
                        if decoded:
                            team_id = decoded['team']
                            service_id = decoded['service']
                            round_id = decoded['round']
                            
                            if status == "OK":
                                self.flags_by_team[str(team_id)] += 1
                                self.flags_by_service[str(service_id)] += 1
                            
                            self.submission_history.append({
                                'timestamp': timestamp,
                                'flag': flag[:6] + "...",
                                'team': team_id,
                                'service': service_id,
                                'round': round_id,
                                'status': status,
                                'message': msg
                            })
                        else:
                            self.submission_history.append({
                                'timestamp': timestamp,
                                'flag': flag[:6] + "..." if len(flag) > 6 else flag,
                                'team': "?",
                                'service': "?",
                                'round': "?",
                                'status': status,
                                'message': msg
                            })
                        
                        print(f"[{status}] {msg}")
                    
                    # Save stats periodically (after every batch)
                    self.save_stats()

    def start_web_server(self):
        """Start the web server with GUI"""
        app = Flask(__name__)
        
        # Templates for the web interface
        @app.route('/')
        def home():
            with self.lock:
                queue_size = self.flag_queue.qsize()
                history = list(reversed(self.submission_history[-100:]))
                
                # Calculate statistics
                total_submitted = len(self.submission_history)
                successful = sum(1 for item in self.submission_history if item['status'] == 'OK')
                success_rate = (successful / total_submitted * 100) if total_submitted > 0 else 0
                
                # Get top teams and services
                top_teams = sorted(self.flags_by_team.items(), key=lambda x: x[1], reverse=True)[:10]
                top_services = sorted(self.flags_by_service.items(), key=lambda x: x[1], reverse=True)[:10]
                
                # Count statuses
                status_counts = Counter(item['status'] for item in self.submission_history)
                
                # Create HTML for the page
                html = f"""
                <!DOCTYPE html>
                <html>
                <head>
                    <title>CTF Flag Submitter Dashboard</title>
                    <meta name="viewport" content="width=device-width, initial-scale=1">
                    <style>
                        body {{ font-family: Arial, sans-serif; margin: 0; padding: 20px; background-color: #f5f5f5; }}
                        .container {{ max-width: 1200px; margin: 0 auto; }}
                        .card {{ background: white; border-radius: 8px; padding: 15px; margin-bottom: 20px; box-shadow: 0 2px 4px rgba(0,0,0,0.1); }}
                        .header {{ display: flex; justify-content: space-between; align-items: center; }}
                        .stats {{ display: flex; flex-wrap: wrap; gap: 15px; }}
                        .stat-box {{ flex: 1; min-width: 150px; background: #f0f8ff; padding: 15px; border-radius: 5px; text-align: center; }}
                        .value {{ font-size: 24px; font-weight: bold; margin: 5px 0; }}
                        table {{ width: 100%; border-collapse: collapse; }}
                        th, td {{ padding: 10px; text-align: left; border-bottom: 1px solid #ddd; }}
                        th {{ background-color: #f2f2f2; }}
                        .status-OK {{ color: green; }}
                        .status-ERROR {{ color: red; }}
                        .status-DUPLICATE {{ color: orange; }}
                        .submit-form {{ display: flex; gap: 10px; margin-bottom: 20px; }}
                        .submit-form input[type="text"] {{ flex: 1; padding: 10px; border: 1px solid #ddd; border-radius: 4px; }}
                        .submit-form button {{ padding: 10px 20px; background-color: #4CAF50; color: white; border: none; border-radius: 4px; cursor: pointer; }}
                        .charts {{ display: grid; grid-template-columns: 1fr 1fr; gap: 20px; }}
                        @media (max-width: 768px) {{ 
                            .charts {{ grid-template-columns: 1fr; }}
                            .stats {{ flex-direction: column; }}
                        }}
                        .refresh {{ padding: 5px 10px; background-color: #2196F3; color: white; border: none; border-radius: 4px; cursor: pointer; }}
                    </style>
                    <script>
                        function refreshPage() {{
                            location.reload();
                        }}
                        
                        // Auto refresh every 30 seconds
                        setInterval(refreshPage, 30000);
                    </script>
                </head>
                <body>
                    <div class="container">
                        <div class="card header">
                            <h1>CTF Flag Submitter Dashboard</h1>
                            <button class="refresh" onclick="refreshPage()">Refresh</button>
                        </div>
                        
                        <div class="card">
                            <h2>Submit Flag</h2>
                            <form action="/submit" method="post" class="submit-form">
                                <input type="text" name="flag" placeholder="Enter flag..." required>
                                <button type="submit">Submit</button>
                            </form>
                        </div>
                        
                        <div class="card">
                            <h2>Statistics</h2>
                            <div class="stats">
                                <div class="stat-box">
                                    <div>Flags in Queue</div>
                                    <div class="value">{queue_size}</div>
                                </div>
                                <div class="stat-box">
                                    <div>Total Submitted</div>
                                    <div class="value">{total_submitted}</div>
                                </div>
                                <div class="stat-box">
                                    <div>Successful</div>
                                    <div class="value">{successful}</div>
                                </div>
                                <div class="stat-box">
                                    <div>Success Rate</div>
                                    <div class="value">{success_rate:.1f}%</div>
                                </div>
                            </div>
                        </div>
                        
                        <div class="charts">
                            <div class="card">
                                <h2>Top Teams</h2>
                                <table>
                                    <tr>
                                        <th>Team ID</th>
                                        <th>Flags</th>
                                    </tr>
                                    {"".join(f"<tr><td>Team {team}</td><td>{count}</td></tr>" for team, count in top_teams)}
                                </table>
                            </div>
                            
                            <div class="card">
                                <h2>Top Services</h2>
                                <table>
                                    <tr>
                                        <th>Service ID</th>
                                        <th>Flags</th>
                                    </tr>
                                    {"".join(f"<tr><td>Service {service}</td><td>{count}</td></tr>" for service, count in top_services)}
                                </table>
                            </div>
                        </div>
                        
                        <div class="card">
                            <h2>Status Counts</h2>
                            <table>
                                <tr>
                                    <th>Status</th>
                                    <th>Count</th>
                                </tr>
                                {"".join(f"<tr><td>{status}</td><td>{count}</td></tr>" for status, count in status_counts.items())}
                            </table>
                        </div>
                        
                        <div class="card">
                            <h2>Recent Submissions</h2>
                            <table>
                                <tr>
                                    <th>Time</th>
                                    <th>Flag</th>
                                    <th>Team</th>
                                    <th>Service</th>
                                    <th>Round</th>
                                    <th>Status</th>
                                    <th>Message</th>
                                </tr>
                                {"".join(f"<tr><td>{item['timestamp']}</td><td>{item['flag']}</td><td>{item['team']}</td><td>{item['service']}</td><td>{item['round']}</td><td class='status-{item['status']}'>{item['status']}</td><td>{item['message']}</td></tr>" for item in history)}
                            </table>
                        </div>
                    </div>
                </body>
                </html>
                """
                return html

        @app.route('/submit', methods=['POST'])
        def http_add_flag():
            flag = request.form.get('flag', '').strip()
            valid, msg = self.validate_flag(flag)
            if valid:
                with self.lock:
                    self.flag_queue.put(flag)
                return redirect(url_for('home'))
            return f"Error: {msg}", 400

        @app.route('/api/queue', methods=['GET'])
        def get_queue():
            with self.lock:
                size = self.flag_queue.qsize()
            return jsonify({"queue_size": size})

        @app.route('/api/stats', methods=['GET'])
        def get_stats():
            with self.lock:
                return jsonify({
                    "queue_size": self.flag_queue.qsize(),
                    "total_submitted": len(self.submission_history),
                    "history": self.submission_history[-100:],
                    "by_team": dict(self.flags_by_team),
                    "by_service": dict(self.flags_by_service)
                })

        @app.route('/api/submit', methods=['POST'])
        def api_add_flag():
            data = request.get_json()
            if not data or 'flag' not in data:
                return jsonify({"status": "ERROR", "message": "No flag provided"}), 400
                
            flag = data['flag'].strip()
            valid, msg = self.validate_flag(flag)
            if valid:
                with self.lock:
                    self.flag_queue.put(flag)
                return jsonify({"status": "OK", "flag": flag[:6] + "..."})
            return jsonify({"status": "ERROR", "message": msg}), 400

        print(f"\n[+] Web server listening on http://localhost:5000")
        app.run(host='0.0.0.0', port=5000, threaded=True)

    def add_flag(self, flag):
        """Add a flag manually"""
        valid, msg = self.validate_flag(flag)
        if valid:
            with self.lock:
                self.flag_queue.put(flag)
                decoded = self.decode_flag(flag)
                team_id = decoded['team'] if decoded else "unknown"
                print(f"[+] Flag accepted: {flag[:6]}... (Team {team_id})")
            return True
        else:
            print(f"[-] Flag rejected: {msg}")
            return False

    def start_cli(self):
        """Command line interface"""
        print(f"""
        === CTF Flag Submitter ===
        Server: {self.SERVER_URL}
        Token: {self.TEAM_TOKEN[:4]}...{self.TEAM_TOKEN[-4:]}
        Web Dashboard: http://localhost:5000
        API Endpoint: http://localhost:5000/api/submit
        ----------------------------
        Commands:
        - 'submit FLAG' to add a flag
        - 'status' to see queue and stats
        - 'exit' to quit
        """)
        
        while True:
            try:
                cmd = input("> ").strip()
                if cmd.lower() == 'exit':
                    break
                elif cmd.lower() == 'status':
                    with self.lock:
                        print(f"Flags in queue: {self.flag_queue.qsize()}")
                        print(f"Total submitted: {len(self.submission_history)}")
                        successful = sum(1 for item in self.submission_history if item['status'] == 'OK')
                        print(f"Successfully submitted: {successful}")
                        if self.submission_history:
                            last = self.submission_history[-1]
                            print(f"Last submission: {last['timestamp']} - {last['status']}")
                elif cmd.startswith('submit '):
                    flag = cmd[7:].strip()
                    self.add_flag(flag)
                else:
                    print("Command not recognized")
            except KeyboardInterrupt:
                self.save_stats()  # Save stats before exiting
                print("\nExiting... Stats saved.")
                break
            except Exception as e:
                print(f"Error: {str(e)}")

        # Final save on exit
        self.save_stats()

if __name__ == "__main__":
    submitter = CTFSubmitter()
    
    # Inform the user about the web interface
    print("\n[+] Web Dashboard is available at http://localhost:5000")
    print("[+] Flag submission API available at http://localhost:5000/api/submit")
    
    submitter.start_cli()