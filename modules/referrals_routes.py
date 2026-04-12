"""
referrals_routes.py — Referral Program Routes
Piney Digital Outreach System — Referral Management
"""

from flask import Blueprint, render_template_string, request, redirect, url_for, jsonify, session
from modules.referrals_db import (
    init_referral_tables, get_referral_settings, save_referral_settings,
    get_or_create_referral_code, track_referral_click, create_referral,
    complete_referral, reward_referral, get_business_referral_stats,
    get_customer_referral_stats
)
from modules.loyalty_db import get_loyalty_business, get_customer, get_or_create_customer_card, add_punch, create_customer

referrals_bp = Blueprint('referrals', __name__, url_prefix='/referrals')


@referrals_bp.route("/business/<biz_id>/settings")
def referral_settings(biz_id: str):
    """Business referral program settings."""
    if not session.get("logged_in"):
        return redirect(url_for("login"))
    
    biz = get_loyalty_business(biz_id)
    if not biz:
        return "Business not found", 404
    
    settings = get_referral_settings(biz_id)
    stats = get_business_referral_stats(biz_id)
    
    return render_template_string("""<!DOCTYPE html><html><head>
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Referral Program - {{ biz_name }}</title>
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:#0f172a;color:#e2e8f0;min-height:100vh}
.layout{display:flex;min-height:100vh}
.sidebar{width:210px;background:#1e293b;padding:0;flex-shrink:0;display:flex;flex-direction:column}
.logo{padding:20px;border-bottom:1px solid #334155}
.logo h1{font-size:15px;font-weight:600;color:#fff}
.nav a{display:flex;align-items:center;gap:10px;padding:9px 20px;font-size:13px;color:#94a3b8;border-left:2px solid transparent;text-decoration:none}
.nav a.active{background:#0f172a;color:#fff;border-left-color:#22c55e}
.dot{width:7px;height:7px;border-radius:50%;background:#334155}
.nav a.active .dot{background:#22c55e}
.main{flex:1;padding:28px}
.topbar{display:flex;justify-content:space-between;align-items:center;margin-bottom:24px}
.panel{background:#1e293b;border-radius:8px;padding:18px;margin-bottom:16px}
.grid2{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:16px}
.form-group{margin-bottom:16px}
.form-group label{display:block;font-size:12px;color:#64748b;margin-bottom:6px}
.form-group input,.form-group select{width:100%;background:#0f172a;border:1px solid #334155;color:#e2e8f0;padding:10px;border-radius:6px;font-size:14px}
.btn{padding:8px 16px;border-radius:6px;font-size:13px;font-weight:500;cursor:pointer;border:none}
.btn-green{background:#166534;color:#86efac}
.toggle{position:relative;width:44px;height:24px;background:#334155;border-radius:99px;cursor:pointer}
.toggle.active{background:#166534}
.toggle::after{content:'';position:absolute;top:2px;left:2px;width:20px;height:20px;background:#fff;border-radius:50%;transition:transform .2s}
.toggle.active::after{transform:translateX(20px)}
.stat{background:#0f172a;padding:16px;border-radius:8px;text-align:center}
.stat-num{font-size:28px;font-weight:600;color:#f1f5f9}
.stat-label{font-size:12px;color:#64748b;margin-top:4px}
.referral-item{background:#0f172a;padding:12px;border-radius:6px;margin-bottom:8px;display:flex;justify-content:space-between}
</style>
</head><body>
<div class="layout">
  <div class="sidebar">
    <div class="logo"><h1>🌲 {{ biz_name }}</h1><p>Referral Program</p></div>
    <nav class="nav">
      <a href="/loyalty/business/{{ biz_id }}"><div class="dot"></div>Loyalty Dashboard</a>
      <a href="/referrals/business/{{ biz_id }}/settings" class="active"><div class="dot"></div>Referral Settings</a>
    </nav>
  </div>
  <div class="main">
    <div class="topbar">
      <h2>Referral Program Settings</h2>
      <span class="badge badge-green">{{ stats.total_referrals }} referrals</span>
    </div>
    
    <div class="grid2" style="margin-bottom:24px">
      <div class="stat">
        <div class="stat-num">{{ stats.total_clicks }}</div>
        <div class="stat-label">Link Clicks</div>
      </div>
      <div class="stat">
        <div class="stat-num">{{ stats.total_referrals }}</div>
        <div class="stat-label">Successful Referrals</div>
      </div>
      <div class="stat">
        <div class="stat-num">{{ stats.conversion_rate }}%</div>
        <div class="stat-label">Conversion Rate</div>
      </div>
      <div class="stat">
        <div class="stat-num">{{ stats.top_referrers|length }}</div>
        <div class="stat-label">Active Referrers</div>
      </div>
    </div>
    
    <div class="grid2">
      <div class="panel">
        <h3>Program Settings</h3>
        <form method="POST" action="/referrals/business/{{ biz_id }}/settings/save">
          <div class="form-group">
            <label>Enable Referral Program</label>
            <div class="toggle {{ 'active' if settings.enabled else '' }}" onclick="this.classList.toggle('active');document.getElementById('enabled').value=this.classList.contains('active')?'1':'0'"></div>
            <input type="hidden" name="enabled" id="enabled" value="{{ settings.enabled }}">
          </div>
          
          <div class="form-group">
            <label>Referrer Reward Type</label>
            <select name="referrer_reward_type">
              <option value="punches" {{ 'selected' if settings.referrer_reward_type=='punches' else '' }}>Loyalty Punches</option>
              <option value="discount" {{ 'selected' if settings.referrer_reward_type=='discount' else '' }}>Discount ($)</option>
              <option value="percent" {{ 'selected' if settings.referrer_reward_type=='percent' else '' }}>Discount (%)</option>
            </select>
          </div>
          
          <div class="form-group">
            <label>Referrer Reward Value</label>
            <input type="number" name="referrer_reward_value" value="{{ settings.referrer_reward_value }}" min="1">
            <p style="font-size:11px;color:#64748b;margin-top:4px">Friend refers → Referrer gets this</p>
          </div>
          
          <div class="form-group">
            <label>New Customer Welcome Bonus</label>
            <select name="referee_reward_type">
              <option value="punches" {{ 'selected' if settings.referee_reward_type=='punches' else '' }}>Loyalty Punches</option>
              <option value="discount" {{ 'selected' if settings.referee_reward_type=='discount' else '' }}>Discount ($)</option>
              <option value="percent" {{ 'selected' if settings.referee_reward_type=='percent' else '' }}>Discount (%)</option>
            </select>
            <input type="number" name="referee_reward_value" value="{{ settings.referee_reward_value }}" min="1" style="margin-top:8px">
            <p style="font-size:11px;color:#64748b;margin-top:4px">New customer gets this on signup</p>
          </div>
          
          <button type="submit" class="btn btn-green">Save Settings</button>
        </form>
      </div>
      
      <div class="panel">
        <h3>Top Referrers</h3>
        {% if stats.top_referrers %}
        {% for ref in stats.top_referrers %}
        <div class="referral-item">
          <div>
            <div style="font-weight:500;color:#f1f5f9">{{ ref.customer_name }}</div>
            <div style="font-size:11px;color:#64748b">Code: <strong>{{ ref.referral_code }}</strong></div>
          </div>
          <div style="text-align:right">
            <div style="font-size:18px;font-weight:600;color:#86efac">{{ ref.referrals }}</div>
            <div style="font-size:11px;color:#64748b">referrals</div>
          </div>
        </div>
        {% endfor %}
        {% else %}
        <p style="color:#64748b;text-align:center;padding:24px">No referrals yet</p>
        {% endif %}
      </div>
    </div>
  </div>
</div>
</body></html>
""", biz_id=biz_id, biz_name=biz['name'], settings=settings, stats=stats)


@referrals_bp.route("/business/<biz_id>/settings/save", methods=["POST"])
def save_referral_settings_route(biz_id: str):
    """Save referral settings."""
    if not session.get("logged_in"):
        return redirect(url_for("login"))
    
    settings = {
        "enabled": request.form.get("enabled") == "1",
        "referrer_reward_type": request.form.get("referrer_reward_type"),
        "referrer_reward_value": int(request.form.get("referrer_reward_value", 2)),
        "referee_reward_type": request.form.get("referee_reward_type"),
        "referee_reward_value": int(request.form.get("referee_reward_value", 1)),
        "max_referrals": None
    }
    
    save_referral_settings(biz_id, settings)
    return redirect(f"/referrals/business/{biz_id}/settings")


@referrals_bp.route("/<biz_id>")
def referral_landing(biz_id: str):
    """Customer referral landing page (when they click a referral link)."""
    code = request.args.get("code")
    
    biz = get_loyalty_business(biz_id)
    if not biz:
        return "Business not found", 404
    
    # Track click
    if code:
        track_referral_click(code, request.remote_addr, request.user_agent.string if request.user_agent else None)
    
    return render_template_string("""<!DOCTYPE html><html><head>
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Join {{ biz_name }} Loyalty</title>
<style>
body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:linear-gradient(135deg,#0f172a 0%,#1e293b 100%);min-height:100vh;display:flex;align-items:center;justify-content:center;padding:20px}
.card{background:#1e293b;border-radius:12px;padding:32px;max-width:400px;width:100%}
h1{font-size:24px;text-align:center;margin-bottom:8px}
.subtitle{text-align:center;color:#94a3b8;margin-bottom:24px}
.form-group{margin-bottom:16px}
.form-group label{display:block;font-size:12px;color:#64748b;margin-bottom:6px}
.form-group input{width:100%;background:#0f172a;border:1px solid #334155;color:#e2e8f0;padding:12px;border-radius:8px;font-size:14px}
.btn{width:100%;padding:14px;background:#166534;color:#86efac;border:none;border-radius:8px;font-size:15px;font-weight:600;cursor:pointer}
.reward-box{background:#16653420;border:1px solid #166534;border-radius:8px;padding:16px;margin-bottom:20px;text-align:center}
.reward-amount{font-size:28px;font-weight:700;color:#86efac}
</style>
</head><body>
<div class="card">
  <h1>🎉 You've Been Referred!</h1>
  <div class="subtitle">Join {{ biz_name }} loyalty program</div>
  
  <div class="reward-box">
    <div style="font-size:13px;color:#94a3b8;margin-bottom:4px">Your Welcome Bonus</div>
    <div class="reward-amount">{{ reward_text }}</div>
  </div>
  
  <form method="POST" action="/referrals/{{ biz_id }}/signup">
    <input type="hidden" name="code" value="{{ code or '' }}">
    <div class="form-group">
      <label>Full Name</label>
      <input type="text" name="name" required placeholder="Your name">
    </div>
    <div class="form-group">
      <label>Phone Number</label>
      <input type="tel" name="phone" required placeholder="For loyalty card">
    </div>
    <div class="form-group">
      <label>Email (optional)</label>
      <input type="email" name="email" placeholder="For rewards">
    </div>
    <button type="submit" class="btn">Join & Claim Reward</button>
  </form>
  
  <p style="text-align:center;font-size:12px;color:#64748b;margin-top:16px">
    By joining, you agree to receive SMS about rewards
  </p>
</div>
</body></html>
""", biz_id=biz_id, biz_name=biz['name'], code=code or '', reward_text="1 Free Punch!")


@referrals_bp.route("/<biz_id>/signup", methods=["POST"])
def referral_signup(biz_id: str):
    """Handle new customer signup via referral."""
    name = request.form.get("name")
    phone = request.form.get("phone")
    email = request.form.get("email")
    code = request.form.get("code")
    
    if not all([name, phone]):
        return redirect(f"/referrals/{biz_id}?code={code}")
    
    # Create customer
    customer_id = create_customer(name=name, email=email, phone=phone)
    
    # Create referral record if code provided
    referral_id = None
    if code:
        # Get referrer from code
        from modules.referrals_db import get_connection
        conn = get_connection()
        c = conn.cursor()
        c.execute("""
            SELECT customer_id FROM referral_codes WHERE code = ?
        """, (code,))
        row = c.fetchone()
        
        if row:
            referrer_id = row["customer_id"]
            referral_id = create_referral(code, referrer_id, customer_id, biz_id)
            
            # Give welcome bonus (1 punch)
            card = get_or_create_customer_card(customer_id, biz_id)
            if card:
                # Add welcome punch
                add_punch(card["id"], punched_by="referral_welcome", notes="Welcome bonus from referral")
        
        conn.close()
    
    # Redirect to customer portal
    return redirect(f"/customer/portal?cust_id={customer_id}&welcome=1")


@referrals_bp.route("/customer/<cust_id>/card")
def customer_referral_card(cust_id: str):
    """Show customer their referral code (integrates with loyalty card page)."""
    biz_id = request.args.get("biz_id")
    
    if not biz_id:
        return redirect("/customer/portal")
    
    cust = get_customer(cust_id)
    stats = get_customer_referral_stats(cust_id, biz_id)
    biz = get_loyalty_business(biz_id)
    
    if not stats.get("code"):
        # Create code
        stats["code"] = get_or_create_referral_code(cust_id, biz_id, cust["name"])
    
    referral_url = f"http://localhost:5000/referrals/{biz_id}?code={stats['code']}"
    
    return render_template_string("""<!DOCTYPE html><html><head>
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Your Referral Card</title>
<style>
body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:#0f172a;color:#e2e8f0;min-height:100vh;padding:20px}
.card{background:#1e293b;border-radius:12px;padding:24px;max-width:500px;margin:0 auto}
h1{font-size:20px;text-align:center;margin-bottom:16px}
.code-box{background:#0f172a;border:2px dashed #166534;border-radius:8px;padding:20px;text-align:center;margin:20px 0}
.code{font-size:32px;font-weight:700;color:#86efac;letter-spacing:2px}
.stats{display:grid;grid-template-columns:repeat(3,1fr);gap:12px;margin:20px 0}
.stat{text-align:center;padding:12px;background:#0f172a;border-radius:6px}
.stat-num{font-size:20px;font-weight:600;color:#f1f5f9}
.stat-label{font-size:11px;color:#64748b;margin-top:4px}
.btn{display:block;padding:12px;border-radius:8px;text-align:center;text-decoration:none;margin-bottom:8px}
.btn-green{background:#166534;color:#86efac}
.btn-blue{background:#1e3a5f;color:#93c5fd}
.share-text{font-size:12px;color:#64748b;text-align:center;margin-top:16px}
</style>
</head><body>
<div class="card">
  <h1>🎁 Your Referral Card</h1>
  <p style="text-align:center;color:#94a3b8">Share your code, earn rewards!</p>
  
  <div class="code-box">
    <div style="font-size:12px;color:#64748b;margin-bottom:8px">Your Code</div>
    <div class="code">{{ stats.code }}</div>
  </div>
  
  <div class="stats">
    <div class="stat">
      <div class="stat-num">{{ stats.clicks }}</div>
      <div class="stat-label">Clicks</div>
    </div>
    <div class="stat">
      <div class="stat-num">{{ stats.conversions }}</div>
      <div class="stat-label">Signups</div>
    </div>
    <div class="stat">
      <div class="stat-num">{{ stats.rewards_earned }}</div>
      <div class="stat-label">Rewards</div>
    </div>
  </div>
  
  <a href="{{ referral_url }}" target="_blank" class="btn btn-green">📋 Copy Referral Link</a>
  <a href="/customer/portal?cust_id={{ cust_id }}" class="btn btn-blue">← Back to My Cards</a>
  
  <div class="share-text">
    Share this link with friends! When they join, you both get rewards.
  </div>
</div>

<script>
// Auto-copy link on page load (optional)
// navigator.clipboard.writeText('{{ referral_url }}');
</script>
</body></html>
""", cust_id=cust_id, biz_id=biz_id, stats=stats, referral_url=referral_url, biz_name=biz['name'])
