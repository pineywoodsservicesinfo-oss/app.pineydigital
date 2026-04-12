"""
admin_overview.py — Unified Admin Dashboard Overview
Piney Digital Outreach System — All Features at a Glance
"""

from flask import Blueprint, render_template_string, session, redirect, url_for
from modules.loyalty_db import get_loyalty_stats
from modules.reviews_db import get_connection  # Would need review stats function
from modules.bookings_db import get_connection  # Would need booking stats function

admin_bp = Blueprint('admin_overview', __name__)


@admin_bp.route("/admin/overview")
def admin_overview():
    """Unified admin dashboard showing all features."""
    if not session.get("logged_in"):
        return redirect(url_for("login"))
    
    # Get stats from all systems
    loyalty_stats = get_loyalty_stats()
    
    # Placeholder for other stats (would add proper functions)
    review_stats = {"total_requests": 0, "average_rating": 0}
    booking_stats = {"total_bookings": 0, "pending": 0}
    
    return render_template_string("""<!DOCTYPE html><html><head>
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Admin Overview - Piney Digital</title>
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:#0f172a;color:#e2e8f0;min-height:100vh}
.layout{display:flex;min-height:100vh}
.sidebar{width:210px;background:#1e293b;padding:0;flex-shrink:0;display:flex;flex-direction:column}
.logo{padding:20px;border-bottom:1px solid #334155}
.logo h1{font-size:15px;font-weight:600;color:#fff}
.nav a{display:flex;align-items:center;gap:10px;padding:9px 20px;font-size:13px;color:#94a3b8;border-left:2px solid transparent;text-decoration:none}
.nav a.active{background:#0f172a;color:#fff;border-left-color:#22c55e}
.main{flex:1;padding:28px}
.topbar{margin-bottom:24px}
.topbar h1{font-size:24px;font-weight:600;color:#f1f5f9}
.topbar p{color:#64748b;margin-top:4px}
.grid4{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:16px;margin-bottom:32px}
.feature-card{background:#1e293b;border-radius:12px;padding:20px;border:1px solid #334155}
.feature-icon{font-size:32px;margin-bottom:12px}
.feature-title{font-size:14px;font-weight:600;color:#f1f5f9;margin-bottom:8px}
.feature-stats{display:flex;gap:16px;margin-bottom:16px}
.feature-stat{text-align:center}
.feature-stat-num{font-size:24px;font-weight:600;color:#f1f5f9}
.feature-stat-label{font-size:11px;color:#64748b;margin-top:2px}
.feature-link{display:inline-block;padding:8px 16px;background:#1e3a5f;color:#93c5fd;border-radius:6px;text-decoration:none;font-size:12px;font-weight:500}
.feature-link:hover{opacity:.9}
.quick-actions{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:16px}
.action-card{background:#1e293b;border-radius:8px;padding:16px;display:flex;align-items:center;gap:12px;cursor:pointer;transition:all .15s}
.action-card:hover{background:#263348;transform:translateY(-2px)}
.action-icon{width:40px;height:40px;border-radius:8px;background:#1e3a5f;display:flex;align-items:center;justify-content:center;font-size:18px}
.action-info{flex:1}
.action-title{font-size:13px;font-weight:500;color:#f1f5f9}
.action-desc{font-size:11px;color:#64748b}
@media(max-width:1024px){.grid4{grid-template-columns:repeat(2,1fr)}.quick-actions{grid-template-columns:1fr}}
@media(max-width:768px){.layout{flex-direction:column}.sidebar{width:100%;flex-shrink:0;border-bottom:1px solid #334155}.nav{display:flex;flex-direction:row;overflow-x:auto;padding:8px 0}.nav a{white-space:nowrap;padding:8px 12px;font-size:12px;border-left:none;border-bottom:2px solid transparent}.nav a.active{border-left:none;border-bottom-color:#22c55e;background:transparent}.main{padding:16px}.grid4{grid-template-columns:1fr}}
</style>
</head><body>
<div class="layout">
  <div class="sidebar">
    <div class="logo"><h1>🌲 Piney Digital</h1><p>Admin Overview</p></div>
    <nav class="nav">
      <a href="/admin/overview" class="active">Overview</a>
      <a href="/">Dashboard</a>
      <a href="/leads">Leads</a>
      <a href="/loyalty">Loyalty</a>
      <a href="/logout">Sign out</a>
    </nav>
  </div>
  <div class="main">
    <div class="topbar">
      <h1>Platform Overview</h1>
      <p>All features at a glance</p>
    </div>
    
    <div class="grid4">
      <!-- Loyalty Program -->
      <div class="feature-card">
        <div class="feature-icon">🎯</div>
        <div class="feature-title">Loyalty Program</div>
        <div class="feature-stats">
          <div class="feature-stat">
            <div class="feature-stat-num">{{ loyalty.total_customers }}</div>
            <div class="feature-stat-label">Customers</div>
          </div>
          <div class="feature-stat">
            <div class="feature-stat-num">{{ loyalty.active_businesses }}</div>
            <div class="feature-stat-label">Businesses</div>
          </div>
          <div class="feature-stat">
            <div class="feature-stat-num">{{ loyalty.total_punches }}</div>
            <div class="feature-stat-label">Punches</div>
          </div>
        </div>
        <a href="/loyalty" class="feature-link">Manage →</a>
      </div>
      
      <!-- Online Booking -->
      <div class="feature-card">
        <div class="feature-icon">📅</div>
        <div class="feature-title">Online Booking</div>
        <div class="feature-stats">
          <div class="feature-stat">
            <div class="feature-stat-num">{{ booking.total }}</div>
            <div class="feature-stat-label">Bookings</div>
          </div>
          <div class="feature-stat">
            <div class="feature-stat-num">{{ booking.pending }}</div>
            <div class="feature-stat-label">Pending</div>
          </div>
          <div class="feature-stat">
            <div class="feature-stat-num">0</div>
            <div class="feature-stat-label">This Week</div>
          </div>
        </div>
        <a href="/bookings/business/biz_05f238ac8973/manage" class="feature-link">Manage →</a>
      </div>
      
      <!-- Review Requests -->
      <div class="feature-card">
        <div class="feature-icon">⭐</div>
        <div class="feature-title">Review Requests</div>
        <div class="feature-stats">
          <div class="feature-stat">
            <div class="feature-stat-num">{{ review.total }}</div>
            <div class="feature-stat-label">Reviews</div>
          </div>
          <div class="feature-stat">
            <div class="feature-stat-num">{{ review.avg or 'N/A' }}</div>
            <div class="feature-stat-label">Avg Rating</div>
          </div>
          <div class="feature-stat">
            <div class="feature-stat-num">0</div>
            <div class="feature-stat-label">Pending</div>
          </div>
        </div>
        <a href="/reviews/business/biz_05f238ac8973/settings" class="feature-link">Manage →</a>
      </div>
      
      <!-- Referral Program -->
      <div class="feature-card">
        <div class="feature-icon">🎁</div>
        <div class="feature-title">Referral Program</div>
        <div class="feature-stats">
          <div class="feature-stat">
            <div class="feature-stat-num">0</div>
            <div class="feature-stat-label">Referrals</div>
          </div>
          <div class="feature-stat">
            <div class="feature-stat-num">0</div>
            <div class="feature-stat-label">Clicks</div>
          </div>
          <div class="feature-stat">
            <div class="feature-stat-num">0%</div>
            <div class="feature-stat-label">Conversion</div>
          </div>
        </div>
        <a href="/referrals/business/biz_05f238ac8973/settings" class="feature-link">Manage →</a>
      </div>
    </div>
    
    <h2 style="font-size:16px;font-weight:600;color:#f1f5f9;margin-bottom:16px">Quick Actions</h2>
    <div class="quick-actions">
      <div class="action-card" onclick="location.href='/book/biz_05f238ac8973'">
        <div class="action-icon">📋</div>
        <div class="action-info">
          <div class="action-title">Test Booking Page</div>
          <div class="action-desc">View customer booking experience</div>
        </div>
      </div>
      <div class="action-card" onclick="location.href='/customer/portal'">
        <div class="action-icon">🎯</div>
        <div class="action-info">
          <div class="action-title">Test Loyalty Cards</div>
          <div class="action-desc">View customer loyalty portal</div>
        </div>
      </div>
      <div class="action-card" onclick="location.href='/reviews/rate/test'">
        <div class="action-icon">⭐</div>
        <div class="action-info">
          <div class="action-title">Test Review Form</div>
          <div class="action-desc">View review rating page</div>
        </div>
      </div>
      <div class="action-card" onclick="location.href='/referrals/biz_05f238ac8973?code=TEST'">
        <div class="action-icon">🎁</div>
        <div class="action-info">
          <div class="action-title">Test Referral Link</div>
          <div class="action-desc">View referral landing page</div>
        </div>
      </div>
      <div class="action-card" onclick="location.href='/bookings/business/biz_05f238ac8973/calendar'">
        <div class="action-icon">📆</div>
        <div class="action-info">
          <div class="action-title">View Calendar</div>
          <div class="action-desc">FullCalendar.js view</div>
        </div>
      </div>
      <div class="action-card" onclick="location.href='/loyalty-landing'">
        <div class="action-icon">🌲</div>
        <div class="action-info">
          <div class="action-title">Loyalty Landing</div>
          <div class="action-desc">Public loyalty page</div>
        </div>
      </div>
    </div>
  </div>
</div>
</body></html>
""", loyalty=loyalty_stats, review=review_stats, booking=booking_stats)
