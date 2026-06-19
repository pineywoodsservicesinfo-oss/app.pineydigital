"""
reviews_routes.py — Review Request Routes for Dashboard
Piney Digital Outreach System — Review Management
"""

from flask import Blueprint, render_template_string, request, redirect, url_for, jsonify
from modules.reviews_db import (
    init_review_tables, get_review_settings, save_review_settings,
    create_review_request, submit_rating, get_business_review_stats,
    get_private_feedback, get_public_reviews, should_send_review_request
)
from modules.reviews_notifications import send_review_request, send_thank_you
from modules.loyalty_db import get_loyalty_business, get_customer, get_connection

reviews_bp = Blueprint('reviews', __name__, url_prefix='/reviews')


@reviews_bp.route("/business/<biz_id>/settings")
def review_settings(biz_id: str):
    """Business review settings page."""
    from flask import session
    if not session.get("logged_in"):
        return redirect(url_for("login"))
    
    biz = get_loyalty_business(biz_id)
    if not biz:
        return "Business not found", 404
    
    settings = get_review_settings(biz_id)
    stats = get_business_review_stats(biz_id)
    
    return render_template_string("""<!DOCTYPE html><html><head>
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Review Settings - {{ biz_name }}</title>
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:#0f172a;color:#e2e8f0;min-height:100vh}
.layout{display:flex;min-height:100vh}
.sidebar{width:210px;background:#1e293b;padding:0;flex-shrink:0;display:flex;flex-direction:column}
.logo{padding:20px;border-bottom:1px solid #334155}
.logo h1{font-size:15px;font-weight:600;color:#fff}
.logo p{font-size:11px;color:#64748b;margin-top:2px}
.nav{padding:12px 0;flex:1}
.nav a{display:flex;align-items:center;gap:10px;padding:9px 20px;font-size:13px;color:#94a3b8;border-left:2px solid transparent;text-decoration:none}
.nav a:hover{background:#0f172a80;color:#cbd5e1}
.nav a.active{background:#0f172a;color:#fff;border-left-color:#22c55e}
.dot{width:7px;height:7px;border-radius:50%;background:#334155;flex-shrink:0}
.nav a.active .dot{background:#22c55e}
.sidebar-footer{padding:16px 20px;border-top:1px solid #334155;font-size:11px;color:#475569}
.main{flex:1;padding:28px}
.topbar{display:flex;justify-content:space-between;align-items:center;margin-bottom:24px}
.topbar h2{font-size:18px;font-weight:500;color:#f1f5f9}
.panel{background:#1e293b;border-radius:8px;padding:18px;margin-bottom:16px}
.panel h3{font-size:11px;color:#64748b;text-transform:uppercase;letter-spacing:.05em;margin-bottom:14px;font-weight:500}
.grid2{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:16px}
.stats{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:12px;margin-bottom:24px}
.stat{background:#1e293b;border-radius:8px;padding:14px 16px}
.stat label{display:block;font-size:11px;color:#64748b;margin-bottom:6px}
.stat .val{font-size:24px;font-weight:500}
.stat .sub{font-size:11px;color:#475569;margin-top:3px}
.form-group{margin-bottom:16px}
.form-group label{display:block;font-size:12px;color:#64748b;margin-bottom:6px}
.form-group input,.form-group textarea,.form-group select{
  width:100%;background:#0f172a;border:1px solid #334155;color:#e2e8f0;
  padding:10px 12px;border-radius:6px;font-size:14px
}
.form-group textarea{min-height:80px;resize:vertical}
.form-row{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px}
.btn{display:inline-flex;align-items:center;gap:6px;padding:8px 16px;
     border-radius:6px;font-size:13px;font-weight:500;cursor:pointer;
     border:none;transition:opacity .15s;text-decoration:none}
.btn:hover{opacity:.85}
.btn-green{background:#166534;color:#86efac}
.btn-blue{background:#1e3a5f;color:#93c5fd}
.toggle{position:relative;width:44px;height:24px;background:#334155;border-radius:99px;cursor:pointer;transition:background .2s}
.toggle.active{background:#166534}
.toggle::after{content:'';position:absolute;top:2px;left:2px;width:20px;height:20px;background:#fff;border-radius:50%;transition:transform .2s}
.toggle.active::after{transform:translateX(20px)}
.badge{display:inline-block;font-size:10px;padding:2px 8px;border-radius:99px;font-weight:500}
.badge-green{background:#166534;color:#86efac}
.badge-amber{background:#78350f;color:#fcd34d}
.badge-red{background:#7f1d1d;color:#fca5a5}
.rating-stars{display:flex;gap:4px;margin:8px 0}
.star{font-size:20px;color:#334155;cursor:pointer}
.star.filled{color:#fbbf24}
.star-row{display:flex;justify-content:space-between;align-items:center;padding:8px 0;border-bottom:1px solid #1e293b}
.star-row:last-child{border-bottom:none}
@media(max-width:768px){
  .layout{flex-direction:column}
  .sidebar{width:100%;flex-shrink:0;border-bottom:1px solid #334155}
  .nav{display:flex;flex-direction:row;overflow-x:auto;padding:8px 0}
  .nav a{white-space:nowrap;padding:8px 12px;font-size:12px;border-left:none;border-bottom:2px solid transparent}
  .nav a.active{border-left:none;border-bottom-color:#22c55e;background:transparent}
  .sidebar-footer{display:none}
  .main{padding:16px}
  .stats{grid-template-columns:repeat(2,1fr)}
  .grid2{grid-template-columns:1fr}
  .form-row{grid-template-columns:1fr}
}
</style>
</head><body>
<div class="layout">
  <div class="sidebar">
    <div class="logo"><h1>🌲 {{ biz_name }}</h1><p>Review Manager</p></div>
    <nav class="nav">
      <a href="/loyalty/business/{{ biz_id }}"><div class="dot"></div>Dashboard</a>
      <a href="/reviews/business/{{ biz_id }}/settings" class="active"><div class="dot"></div>Review settings</a>
      <a href="/reviews/business/{{ biz_id }}/inbox"><div class="dot"></div>Feedback inbox</a>
    </nav>
    <div class="sidebar-footer">Powered by LoyaltyLoop</div>
  </div>
  <div class="main">
    <div class="topbar">
      <h2>Review Request Settings</h2>
      <span class="badge badge-blue">{{ stats.average_rating }}★ avg · {{ stats.total_ratings }} reviews</span>
    </div>
    
    <div class="stats">
      <div class="stat"><label>Requests sent</label><div class="val">{{ stats.total_requests }}</div><div class="sub">total requests</div></div>
      <div class="stat"><label>Response rate</label><div class="val">{{ stats.response_rate }}%</div><div class="sub">{{ stats.total_ratings }} ratings</div></div>
      <div class="stat"><label>Public reviews</label><div class="val">{{ stats.public_count }}</div><div class="sub">4-5 stars</div></div>
      <div class="stat"><label>Private feedback</label><div class="val">{{ stats.private_count }}</div><div class="sub">1-3 stars</div></div>
    </div>
    
    <div class="grid2">
      <div class="panel">
        <h3>Enable/Disable</h3>
        <form method="POST" action="/reviews/business/{{ biz_id }}/settings/save">
          <div class="form-group">
            <label>Review Requests</label>
            <div class="toggle {{ 'active' if settings.enabled else '' }}" onclick="this.classList.toggle('active');document.getElementById('enabled').value=this.classList.contains('active')?'1':'0'"></div>
            <input type="hidden" name="enabled" id="enabled" value="{{ settings.enabled }}">
            <p style="font-size:12px;color:#64748b;margin-top:8px">Automatically send review requests after visits</p>
          </div>
          
          <div class="form-group">
            <label>Delay (hours after visit)</label>
            <input type="number" name="delay_hours" value="{{ settings.delay_hours }}" min="1" max="72">
            <p style="font-size:12px;color:#64748b;margin-top:8px">Wait this long before sending request</p>
          </div>
          
          <div class="form-group">
            <label>Minimum stars for public review</label>
            <select name="min_stars_public">
              <option value="3" {{ 'selected' if settings.min_stars_public==3 else '' }}>3+ stars</option>
              <option value="4" {{ 'selected' if settings.min_stars_public==4 else '' }}>4+ stars (recommended)</option>
              <option value="5" {{ 'selected' if settings.min_stars_public==5 else '' }}>5 stars only</option>
            </select>
            <p style="font-size:12px;color:#64748b;margin-top:8px">Ratings at or above this go to Google/Yelp</p>
          </div>
          
          <button type="submit" class="btn btn-green">Save Settings</button>
        </form>
      </div>
      
      <div class="panel">
        <h3>Review Links</h3>
        <form method="POST" action="/reviews/business/{{ biz_id }}/settings/save">
          <div class="form-group">
            <label>Google Review Link</label>
            <input type="url" name="google_url" value="{{ settings.google_url or '' }}" placeholder="https://g.page/r/.../review">
            <p style="font-size:12px;color:#64748b;margin-top:8px">Customers with 4+ stars are sent here</p>
          </div>
          
          <div class="form-group">
            <label>Yelp Review Link (optional)</label>
            <input type="url" name="yelp_url" value="{{ settings.yelp_url or '' }}" placeholder="https://yelp.com/...">
          </div>
          
          <div class="form-group">
            <label>Custom SMS Message</label>
            <textarea name="custom_message" placeholder="Hi {name}! Thanks for visiting {business} today...">{{ settings.custom_message or '' }}</textarea>
            <p style="font-size:12px;color:#64748b;margin-top:8px">Use {name} and {business} placeholders</p>
          </div>
          
          <button type="submit" class="btn btn-blue">Save Links</button>
        </form>
      </div>
    </div>
    
    <div class="panel">
      <h3>Recent Ratings</h3>
      {% if stats.recent_ratings %}
      <div style="display:flex;flex-direction:column;gap:8px">
      {% for r in stats.recent_ratings %}
        <div class="star-row">
          <div>
            <div style="font-weight:500;color:#f1f5f9">{{ r.customer_name }}</div>
            <div style="font-size:11px;color:#64748b">{{ (r.submitted_at or '')[:16] }}</div>
          </div>
          <div>
            <span class="badge {{ 'badge-green' if r.stars>=4 else 'badge-red' }}">{{ r.stars }} stars</span>
            {% if r.is_public %}<span class="badge badge-blue">Public</span>{% endif %}
          </div>
        </div>
      {% endfor %}
      </div>
      {% else %}
      <p style="color:#64748b;text-align:center;padding:24px">No ratings yet</p>
      {% endif %}
    </div>
  </div>
</div>
</body></html>
""", biz_id=biz_id, biz_name=biz['name'], settings=settings, stats=stats)


@reviews_bp.route("/business/<biz_id>/settings/save", methods=["POST"])
def save_review_settings_route(biz_id: str):
    """Save review settings."""
    from flask import session
    if not session.get("logged_in"):
        return redirect(url_for("login"))
    
    settings = {
        "enabled": request.form.get("enabled") == "1",
        "delay_hours": int(request.form.get("delay_hours", 2)),
        "google_url": request.form.get("google_url"),
        "yelp_url": request.form.get("yelp_url"),
        "custom_message": request.form.get("custom_message"),
        "min_stars_public": int(request.form.get("min_stars_public", 4))
    }
    
    save_review_settings(biz_id, settings)
    return redirect(f"/reviews/business/{biz_id}/settings")


@reviews_bp.route("/business/<biz_id>/inbox")
def review_inbox(biz_id: str):
    """Business feedback inbox (private + public)."""
    from flask import session
    if not session.get("logged_in"):
        return redirect(url_for("login"))
    
    biz = get_loyalty_business(biz_id)
    if not biz:
        return "Business not found", 404
    
    private = get_private_feedback(biz_id)
    public = get_public_reviews(biz_id)
    
    return render_template_string("""<!DOCTYPE html><html><head>
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Feedback Inbox - {{ biz_name }}</title>
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:#0f172a;color:#e2e8f0;min-height:100vh}
.layout{display:flex;min-height:100vh}
.sidebar{width:210px;background:#1e293b;padding:0;flex-shrink:0;display:flex;flex-direction:column}
.logo{padding:20px;border-bottom:1px solid #334155}
.logo h1{font-size:15px;font-weight:600;color:#fff}
.logo p{font-size:11px;color:#64748b;margin-top:2px}
.nav{padding:12px 0;flex:1}
.nav a{display:flex;align-items:center;gap:10px;padding:9px 20px;font-size:13px;color:#94a3b8;border-left:2px solid transparent;text-decoration:none}
.nav a:hover{background:#0f172a80;color:#cbd5e1}
.nav a.active{background:#0f172a;color:#fff;border-left-color:#22c55e}
.dot{width:7px;height:7px;border-radius:50%;background:#334155;flex-shrink:0}
.nav a.active .dot{background:#22c55e}
.sidebar-footer{padding:16px 20px;border-top:1px solid #334155;font-size:11px;color:#475569}
.main{flex:1;padding:28px}
.topbar{display:flex;justify-content:space-between;align-items:center;margin-bottom:24px}
.topbar h2{font-size:18px;font-weight:500;color:#f1f5f9}
.panel{background:#1e293b;border-radius:8px;padding:18px;margin-bottom:16px}
.panel h3{font-size:11px;color:#64748b;text-transform:uppercase;letter-spacing:.05em;margin-bottom:14px;font-weight:500}
.grid2{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:16px}
.badge{display:inline-block;font-size:10px;padding:2px 8px;border-radius:99px;font-weight:500}
.badge-green{background:#166534;color:#86efac}
.badge-amber{background:#78350f;color:#fcd34d}
.badge-red{background:#7f1d1d;color:#fca5a5}
.feedback-item{background:#0f172a;border-radius:6px;padding:12px;margin-bottom:8px}
.feedback-header{display:flex;justify-content:space-between;align-items:center;margin-bottom:8px}
.feedback-name{font-weight:500;color:#f1f5f9}
.feedback-stars{font-size:18px}
.feedback-text{color:#94a3b8;font-size:13px;line-height:1.5}
.feedback-meta{display:flex;gap:12px;margin-top:8px;font-size:11px;color:#64748b}
@media(max-width:768px){
  .layout{flex-direction:column}
  .sidebar{width:100%;flex-shrink:0;border-bottom:1px solid #334155}
  .nav{display:flex;flex-direction:row;overflow-x:auto;padding:8px 0}
  .nav a{white-space:nowrap;padding:8px 12px;font-size:12px;border-left:none;border-bottom:2px solid transparent}
  .nav a.active{border-left:none;border-bottom-color:#22c55e;background:transparent}
  .sidebar-footer{display:none}
  .main{padding:16px}
  .grid2{grid-template-columns:1fr}
}
</style>
</head><body>
<div class="layout">
  <div class="sidebar">
    <div class="logo"><h1>🌲 {{ biz_name }}</h1><p>Review Manager</p></div>
    <nav class="nav">
      <a href="/loyalty/business/{{ biz_id }}"><div class="dot"></div>Dashboard</a>
      <a href="/reviews/business/{{ biz_id }}/settings"><div class="dot"></div>Review settings</a>
      <a href="/reviews/business/{{ biz_id }}/inbox" class="active"><div class="dot"></div>Feedback inbox</a>
    </nav>
    <div class="sidebar-footer">Powered by LoyaltyLoop</div>
  </div>
  <div class="main">
    <div class="topbar">
      <h2>Feedback Inbox</h2>
      <div>
        <span class="badge badge-red">{{ private|length }} private</span>
        <span class="badge badge-green">{{ public|length }} public</span>
      </div>
    </div>
    
    <div class="grid2">
      <div class="panel">
        <h3>Private Feedback (1-3 stars)</h3>
        {% if private %}
        {% for f in private %}
        <div class="feedback-item">
          <div class="feedback-header">
            <div class="feedback-name">{{ f.customer_name }}</div>
            <div class="feedback-stars">{{ '⭐' * f.stars }}</div>
          </div>
          {% if f.feedback %}
          <div class="feedback-text">"{{ f.feedback }}"</div>
          {% endif %}
          <div class="feedback-meta">
            <span>{{ (f.submitted_at or '')[:16] }}</span>
            {% if f.phone %}<span>📞 {{ f.phone }}</span>{% endif %}
          </div>
        </div>
        {% endfor %}
        {% else %}
        <p style="color:#64748b;text-align:center;padding:24px">No private feedback yet</p>
        {% endif %}
      </div>
      
      <div class="panel">
        <h3>Public Reviews (4-5 stars)</h3>
        {% if public %}
        {% for f in public %}
        <div class="feedback-item">
          <div class="feedback-header">
            <div class="feedback-name">{{ f.customer_name }}</div>
            <div class="feedback-stars">{{ '⭐' * f.stars }}</div>
          </div>
          {% if f.feedback %}
          <div class="feedback-text">"{{ f.feedback }}"</div>
          {% endif %}
          <div class="feedback-meta">
            <span>{{ (f.submitted_at or '')[:16] }}</span>
            <span>Sent to Google/Yelp</span>
          </div>
        </div>
        {% endfor %}
        {% else %}
        <p style="color:#64748b;text-align:center;padding:24px">No public reviews yet</p>
        {% endif %}
      </div>
    </div>
  </div>
</div>
</body></html>
""", biz_id=biz_id, biz_name=biz['name'], private=private, public=public)


@reviews_bp.route("/rate/<request_id>", methods=["GET", "POST"])
def rate_review(request_id: str):
    """Customer rating page (smart routing)."""
    conn = get_connection()
    c = conn.cursor()
    
    # Get request info
    c.execute("""
        SELECT rr.*, bs.google_url, bs.yelp_url, bs.min_stars_public,
               c.name as customer_name, b.name as business_name
        FROM review_requests rr
        JOIN review_settings bs ON rr.business_id = bs.business_id
        JOIN loyalty_customers c ON rr.customer_id = c.id
        JOIN loyalty_businesses b ON rr.business_id = b.id
        WHERE rr.id = ?
    """, (request_id,))
    data = c.fetchone()
    conn.close()
    
    if not data:
        return "Request not found", 404
    
    # Mark as opened
    from modules.reviews_db import mark_request_opened
    mark_request_opened(request_id)
    
    # Handle POST (rating submitted)
    if request.method == "POST":
        stars = int(request.form.get("stars", 0))
        feedback = request.form.get("feedback", "")
        
        # Determine if public or private
        is_public = stars >= data["min_stars_public"]
        
        # Submit rating
        rating_id = submit_rating(request_id, stars, feedback, is_public)
        
        # Send thank you SMS
        from modules.reviews_notifications import send_thank_you
        if data.get("phone"):
            send_thank_you(data["phone"], data["customer_name"], data["business_name"], stars)
        
        # Redirect to Google/Yelp if public
        if is_public and data["google_url"]:
            return render_template_string("""<!DOCTYPE html><html><head>
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Thank You!</title>
<style>
body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:#0f172a;color:#e2e8f0;min-height:100vh;display:flex;align-items:center;justify-content:center;text-align:center;padding:20px}
.card{background:#1e293b;border-radius:12px;padding:32px;max-width:400px}
h1{font-size:24px;margin-bottom:12px;color:#86efac}
p{font-size:14px;color:#94a3b8;margin-bottom:24px;line-height:1.5}
.btn{display:inline-block;padding:12px 24px;background:#166534;color:#86efac;border-radius:8px;text-decoration:none;font-weight:500}
.btn:hover{opacity:.9}
</style>
</head><body>
<div class="card">
  <h1>Thank You! 🎉</h1>
  <p>Your feedback helps us improve. Would you mind sharing your experience on Google?</p>
  <a href="{{ google_url }}" class="btn" target="_blank">Write Google Review</a>
</div>
<script>
// Auto-redirect after 3 seconds
setTimeout(() => { window.open('{{ google_url }}', '_blank'); }, 3000);
</script>
</body></html>
""", google_url=data["google_url"])
        else:
            return render_template_string("""<!DOCTYPE html><html><head>
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Thank You!</title>
<style>
body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:#0f172a;color:#e2e8f0;min-height:100vh;display:flex;align-items:center;justify-content:center;text-align:center;padding:20px}
.card{background:#1e293b;border-radius:12px;padding:32px;max-width:400px}
h1{font-size:24px;margin-bottom:12px;color:#86efac}
p{font-size:14px;color:#94a3b8;line-height:1.5}
</style>
</head><body>
<div class="card">
  <h1>Thank You! 🙏</h1>
  <p>Your feedback is valuable to us. We appreciate you taking the time!</p>
</div>
</body></html>
""")
    
    # GET - show rating form
    return render_template_string("""<!DOCTYPE html><html><head>
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Rate Your Experience</title>
<style>
body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:#0f172a;color:#e2e8f0;min-height:100vh;display:flex;align-items:center;justify-content:center;padding:20px}
.card{background:#1e293b;border-radius:12px;padding:32px;max-width:400px;width:100%}
h1{font-size:20px;margin-bottom:8px;text-align:center}
p{font-size:13px;color:#94a3b8;text-align:center;margin-bottom:24px}
.stars{display:flex;justify-content:center;gap:8px;margin-bottom:24px}
.star{font-size:32px;color:#334155;cursor:pointer;transition:color .15s}
.star:hover,.star.active{color:#fbbf24}
textarea{width:100%;background:#0f172a;border:1px solid #334155;color:#e2e8f0;padding:12px;border-radius:8px;font-size:14px;min-height:80px;resize:vertical;margin-bottom:16px}
.btn{width:100%;padding:12px;background:#166534;color:#86efac;border:none;border-radius:8px;font-size:14px;font-weight:500;cursor:pointer}
.btn:hover{opacity:.9}
</style>
</head><body>
<div class="card">
  <h1>How was your visit?</h1>
  <p>{{ business_name }} values your feedback</p>
  <form method="POST">
    <div class="stars" id="star-rating">
      <span class="star" data-value="1">★</span>
      <span class="star" data-value="2">★</span>
      <span class="star" data-value="3">★</span>
      <span class="star" data-value="4">★</span>
      <span class="star" data-value="5">★</span>
    </div>
    <input type="hidden" name="stars" id="stars-input" required>
    <textarea name="feedback" placeholder="Tell us about your experience (optional)"></textarea>
    <button type="submit" class="btn">Submit Review</button>
  </form>
</div>
<script>
const stars = document.querySelectorAll('.star');
const input = document.getElementById('stars-input');
stars.forEach((star, idx) => {
  star.addEventListener('click', () => {
    input.value = idx + 1;
    stars.forEach((s, i) => {
      s.classList.toggle('active', i <= idx);
    });
  });
});
</script>
</body></html>
""", business_name=data["business_name"], request_id=request_id)
