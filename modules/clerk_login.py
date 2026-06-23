"""
clerk_login.py — Fixed Clerk Authentication Module for FieldPulse

This module provides a working Clerk integration using the correct CDN loading
pattern with UI components properly initialized.

Required Clerk Dashboard Setup:
1. JWT Template named "fieldpulse-template" must exist
2. Publishable and Secret keys in environment variables
"""

# Template for the Clerk login page - uses proper CDN loading
CLERK_LOGIN_TEMPLATE = """
<!DOCTYPE html>
<html lang="en" class="dark">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>FieldPulse — Sign In</title>
    {tailwind_cdn}
    {custom_css}
</head>
<body class="bg-slate-900 min-h-screen flex items-center justify-center">
    <div class="w-full max-w-md px-6">
        <div class="bg-slate-800 rounded-2xl shadow-2xl p-8 fade-in">
            <div class="text-center mb-8">
                <div class="w-16 h-16 bg-gradient-to-br from-emerald-400 to-emerald-600 rounded-xl mx-auto mb-4 flex items-center justify-center">
                    <svg class="w-8 h-8 text-white" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M13 10V3L4 14h7v7l9-11h-7z"/>
                    </svg>
                </div>
                <h1 class="text-2xl font-bold text-white">Sign In to FieldPulse</h1>
                <p class="text-slate-400 mt-1">Secure authentication powered by Clerk</p>
            </div>

            <div id="auth-container" class="space-y-4">
                <button id="sign-in-btn" class="w-full py-3 bg-emerald-500 hover:bg-emerald-600 text-white font-semibold rounded-xl transition flex items-center justify-center gap-2 opacity-50 cursor-not-allowed" disabled>
                    <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M11 16l-4-4m0 0l4-4m-4 4h14m-5 4v1a3 3 0 01-3 3H6a3 3 0 01-3-3V7a3 3 0 013-3h7a3 3 0 013 3v1"/>
                    </svg>
                    <span id="sign-in-text">Loading...</span>
                </button>
                <button id="sign-up-btn" class="w-full py-3 bg-slate-700 hover:bg-slate-600 text-white font-semibold rounded-xl transition opacity-50 cursor-not-allowed" disabled>
                    Create Account
                </button>
            </div>

            <div id="loading" class="hidden text-center py-8">
                <div class="animate-spin w-8 h-8 border-2 border-emerald-500 border-t-transparent rounded-full mx-auto mb-4"></div>
                <p class="text-slate-400">Authenticating...</p>
            </div>

            <div id="error" class="hidden mt-4 p-4 bg-red-500/10 border border-red-500/20 rounded-lg text-red-400 text-sm"></div>

            <p class="center text-slate-600 text-xs mt-6">
                <a href="/legacy-login" class="hover:text-slate-500">Admin: Use legacy login</a>
            </p>
        </div>
    </div>

    <script>
        // Configuration - injected from Python
        window.CLERK_CONFIG = {{
            publishableKey: "{clerk_pub_key}",
            apiUrl: "{app_domain}/api/clerk-verify",
            dashboardUrl: "{app_domain}/dashboard",
            onboardingUrl: "{app_domain}/clerk-onboarding"
        }};
    </script>

    <!-- Load Clerk from their CDN with proper domain -->
    <script src="https://{clerk_domain}/npm/@clerk/clerk-js@latest/dist/clerk.browser.js"
            data-clerk-publishable-key="{clerk_pub_key}"
            crossorigin="anonymous"
            async>
    </script>

    <script>
        // Wait for Clerk to load
        window.addEventListener('load', function() {{
            console.log('[Clerk] Page loaded, checking for Clerk...');

            // Give Clerk a moment to initialize
            setTimeout(initClerk, 500);
        }});

        async function initClerk() {{
            const signInBtn = document.getElementById('sign-in-btn');
            const signUpBtn = document.getElementById('sign-up-btn');
            const signInText = document.getElementById('sign-in-text');

            try {{
                console.log('[Clerk] Initializing...');

                // Check if Clerk loaded
                if (typeof Clerk === 'undefined') {{
                    throw new Error('Clerk SDK not loaded');
                }}

                console.log('[Clerk] SDK found');

                // Wait for Clerk to be ready
                if (!Clerk.loaded) {{
                    console.log('[Clerk] Waiting for Clerk to be ready...');
                    await Clerk.load();
                }}

                console.log('[Clerk] Ready');

                // Check if already signed in
                if (Clerk.user) {{
                    console.log('[Clerk] User already signed in:', Clerk.user);
                    await handleAuthenticatedUser();
                    return;
                }}

                // Enable buttons
                signInBtn.disabled = false;
                signInBtn.classList.remove('opacity-50', 'cursor-not-allowed');
                signUpBtn.disabled = false;
                signUpBtn.classList.remove('opacity-50', 'cursor-not-allowed');
                signInText.textContent = 'Sign In';

                // Attach event listeners
                signInBtn.addEventListener('click', function(e) {{
                    e.preventDefault();
                    openClerkSignIn();
                }});

                signUpBtn.addEventListener('click', function(e) {{
                    e.preventDefault();
                    openClerkSignUp();
                }});

                // Listen for auth state changes
                Clerk.addListener(function(ev) {{
                    console.log('[Clerk] Auth state changed:', ev);
                    if (ev.user && ev.session) {{
                        handleAuthenticatedUser();
                    }}
                }});

                console.log('[Clerk] Event listeners attached');

            }} catch (err) {{
                console.error('[Clerk] Initialization error:', err);
                showError('Failed to initialize: ' + err.message);
                signInText.textContent = 'Error loading auth';
            }}
        }}

        function openClerkSignIn() {{
            console.log('[Clerk] Opening sign in modal');

            if (!Clerk) {{
                showError('Auth not initialized');
                return;
            }}

            try {{
                Clerk.openSignIn({{
                    routing: 'virtual',
                    redirectUrl: window.CLERK_CONFIG.dashboardUrl,
                    afterSignInUrl: window.CLERK_CONFIG.dashboardUrl,
                    appearance: {{
                        variables: {{
                            colorPrimary: '#10b981',
                            colorBackground: '#1e293b',
                            colorText: '#ffffff',
                            colorTextSecondary: '#94a3b8',
                            colorInputBackground: '#0f172a',
                            colorInputBorder: '#334155',
                            borderRadius: '0.75rem',
                        }}
                    }}
                }});
                console.log('[Clerk] Sign in modal opened');
            }} catch (err) {{
                console.error('[Clerk] Failed to open sign in:', err);
                showError('Failed to open sign in: ' + err.message);
            }}
        }}

        function openClerkSignUp() {{
            console.log('[Clerk] Opening sign up modal');

            if (!Clerk) {{
                showError('Auth not initialized');
                return;
            }}

            try {{
                Clerk.openSignUp({{
                    routing: 'virtual',
                    redirectUrl: window.CLERK_CONFIG.onboardingUrl,
                    afterSignUpUrl: window.CLERK_CONFIG.onboardingUrl,
                    appearance: {{
                        variables: {{
                            colorPrimary: '#10b981',
                            colorBackground: '#1e293b',
                            colorText: '#ffffff',
                            colorTextSecondary: '#94a3b8',
                            colorInputBackground: '#0f172a',
                            colorInputBorder: '#334155',
                            borderRadius: '0.75rem',
                        }}
                    }}
                }});
                console.log('[Clerk] Sign up modal opened');
            }} catch (err) {{
                console.error('[Clerk] Failed to open sign up:', err);
                showError('Failed to open sign up: ' + err.message);
            }}
        }}

        async function handleAuthenticatedUser() {{
            const authContainer = document.getElementById('auth-container');
            const loading = document.getElementById('loading');

            authContainer.classList.add('hidden');
            loading.classList.remove('hidden');

            try {{
                // Get JWT token from Clerk using the custom template
                const token = await Clerk.session.getToken({{ template: "fieldpulse-template" }});

                if (!token) {{
                    throw new Error('No token received from Clerk');
                }}

                console.log('[Clerk] Got token, verifying with backend...');

                // Send token to Flask backend
                const response = await fetch(window.CLERK_CONFIG.apiUrl, {{
                    method: 'POST',
                    headers: {{'Content-Type': 'application/json'}},
                    body: JSON.stringify({{token: token}})
                }});

                const data = await response.json();
                console.log('[Clerk] Backend response:', response.status, data);

                if (response.ok && data.success) {{
                    console.log('[Clerk] Auth successful');
                    window.location.href = data.redirect || window.CLERK_CONFIG.dashboardUrl;
                }} else {{
                    throw new Error(data.error || 'Authentication failed');
                }}

            }} catch (err) {{
                console.error('[Clerk] Auth error:', err);
                showError(err.message);
                loading.classList.add('hidden');
                authContainer.classList.remove('hidden');

                // Sign out if backend verification failed
                if (Clerk) {{
                    Clerk.signOut();
                }}
            }}
        }}

        function showError(msg) {{
            console.error('[Clerk] Error:', msg);
            const errorDiv = document.getElementById('error');
            errorDiv.textContent = msg;
            errorDiv.classList.remove('hidden');
        }}
    </script>
</body>
</html>
"""


def get_clerk_domain(publishable_key: str) -> str:
    """Extract Clerk Frontend API domain from publishable key."""
    try:
        import base64

        # Key format: pk_<env>_<base64data>
        parts = publishable_key.split('_')
        if len(parts) < 3:
            return 'frontend-api.clerk.dev'

        encoded = parts[2] if len(parts) > 2 else ''
        if not encoded:
            return 'frontend-api.clerk.dev'

        # Add padding if needed
        padding = 4 - len(encoded) % 4
        if padding != 4:
            encoded += '=' * padding

        decoded = base64.b64decode(encoded).decode('utf-8')
        # Remove null bytes and trailing $
        decoded = decoded.replace('\x00', '').rstrip('$')

        # Ensure it has a dot (is a domain)
        if '.' in decoded:
            return decoded
    except Exception:
        pass

    return 'frontend-api.clerk.dev'


def render_clerk_login_page(clerk_pub_key: str, app_domain: str, tailwind_cdn: str, custom_css: str) -> str:
    """
    Render the Clerk login page with proper UI bundle loading.

    Args:
        clerk_pub_key: Clerk publishable key (pk_test_... or pk_live_...)
        app_domain: Your app's domain (e.g., https://fieldpulse-development.up.railway.app)
        tailwind_cdn: HTML for Tailwind CSS CDN
        custom_css: Your custom CSS styles

    Returns:
        Complete HTML page as string
    """
    clerk_domain = get_clerk_domain(clerk_pub_key)

    return CLERK_LOGIN_TEMPLATE.format(
        clerk_pub_key=clerk_pub_key,
        clerk_domain=clerk_domain,
        app_domain=app_domain.rstrip('/'),
        tailwind_cdn=tailwind_cdn,
        custom_css=custom_css
    )
