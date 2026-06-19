#!/usr/bin/env python3
"""
Start the FieldPulse dashboard on a specific port.
Usage: python start_dashboard.py [port]
"""

import os
import sys

# Set required environment variables
os.environ.setdefault('DASHBOARD_PASSWORD', 'MasKatana@1')
os.environ.setdefault('DASHBOARD_SECRET', '236862338a4199b8ebd752a594af0920f1e6f6e48845b9549388b493f9b33a3e')

# Get port from args or use default
port = int(sys.argv[1]) if len(sys.argv) > 1 else 5000

print(f"""
╔══════════════════════════════════════════════════════════════╗
║                    FieldPulse Dashboard                      ║
╠══════════════════════════════════════════════════════════════╣
║                                                              ║
║  🚀 Starting server...                                         ║
║                                                              ║
║  📍 Local URL:   http://localhost:{port}                     ║
║  🌐 Network URL: http://0.0.0.0:{port}                      ║
║                                                              ║
║  📋 FieldPulse Login:                                         ║
║     http://localhost:{port}/fieldpulse                      ║
║                                                              ║
║  🔐 Demo Credentials:                                         ║
║     Email:    owner@demolandscaping.com                     ║
║     Password: demo123                                         ║
║                                                              ║
╚══════════════════════════════════════════════════════════════╝
""")

# Import and run dashboard
from dashboard import app

if __name__ == "__main__":
    app.run(host='0.0.0.0', port=port, debug=False)
