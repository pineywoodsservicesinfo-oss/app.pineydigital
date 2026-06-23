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
        // Derive Clerk Frontend API domain from publishable key
        // pk_test_xxx -> https://xxx.clerk.accounts.dev
        (function() {{
            const pubKey = "{clerk_pub_key}";
            let domain = 'frontend-api.clerk.dev';  // fallback

            if (pubKey && pubKey.includes('_')) {{
                try {{
                    const encoded = pubKey.split('_').pop();
                    if (encoded) {{
                        // Add padding if needed
                        let padded = encoded;
                        while (padded.length % 4 !== 0) padded += '=';
                        const decoded = atob(padded);
                        // Remove null bytes and trailing $
                        const clean = decoded.replace(/\\x00/g, '').replace(/\\$/g, '');
                        if (clean && clean.includes('.')) {{
                            domain = clean;
                        }}
                    }}
                }} catch (e) {{
                    console.error('[Clerk] Failed to decode domain:', e);
                }}
            }}

            // Store for later use
            window.__CLERK_DOMAIN = domain;

            // Load scripts dynamically
            const loadScript = (src) => {{
                return new Promise((resolve, reject) => {{
                    const script = document.createElement('script');
                    script.src = src;
                    script.crossOrigin = 'anonymous';
                    script.async = false;  // Load in order
                    script.onload = resolve;
                    script.onerror = () => reject(new Error('Failed to load: ' + src));
                    document.head.appendChild(script);
                }});
            }};

            // Load UI bundle first, then clerk-js
            window.__CLERK_LOADER = Promise.resolve()
                .then(() => loadScript(`https://${{domain}}/npm/@clerk/ui@latest/dist/ui.browser.js`))
                .then(() => loadScript(`https://${{domain}}/npm/@clerk/clerk-js@latest/dist/clerk.browser.js`))
                .then(() => console.log('[Clerk] Scripts loaded from', domain))
                .catch(err => console.error('[Clerk] Script loading failed:', err));
        }})();
    </script>

    <script>
        // Configuration
        const CONFIG = {{
            publishableKey: "{clerk_pub_key}",
            apiUrl: "{app_domain}/api/clerk-verify",
            dashboardUrl: "{app_domain}/dashboard",
            onboardingUrl: "{app_domain}/clerk-onboarding"
        }};

        let clerkInstance = null;

        async function initClerk() {{
            const signInBtn = document.getElementById('sign-in-btn');
            const signUpBtn = document.getElementById('sign-up-btn');
            const signInText = document.getElementById('sign-in-text');

            try {{
                console.log('[Clerk] Initializing...');

                // Wait for scripts to load (they were loaded dynamically above)
                if (window.__CLERK_LOADER) {{
                    console.log('[Clerk] Waiting for scripts to load...');
                    await window.__CLERK_LOADER;
                    await new Promise(resolve => setTimeout(resolve, 100));  // Small delay for script execution
                }}

                // Check if Clerk loaded
                if (typeof Clerk === 'undefined') {{
                    throw new Error('Clerk SDK not loaded. Check CDN URLs. Domain: ' + (window.__CLERK_DOMAIN || 'unknown'));
                }}

                // Check if UI bundle loaded (required for modals)
                if (typeof window.__internal_ClerkUICtor === 'undefined') {{
                    console.warn('[Clerk] UI bundle not loaded. Waiting...');
                    // Wait a bit for UI bundle to load
                    await new Promise(resolve => setTimeout(resolve, 500));
                    if (typeof window.__internal_ClerkUICtor === 'undefined') {{
                        throw new Error('Clerk UI bundle failed to load. Check @clerk/ui CDN URL.');
                    }}
                }}

                // Create Clerk instance
                clerkInstance = new Clerk(CONFIG.publishableKey);

                // Load with UI components - THIS IS THE KEY FIX
                await clerkInstance.load({{
                    ui: {{ ClerkUI: window.__internal_ClerkUICtor }},  // REQUIRED for modals
                    routing: 'virtual',
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

                console.log('[Clerk] Loaded successfully with UI components');

                // Check if already signed in
                if (clerkInstance.user) {{
                    console.log('[Clerk] User already signed in:', clerkInstance.user);
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
                signInBtn.addEventListener('click', (e) => {{
                    e.preventDefault();
                    openClerkModal('signIn');
                }});

                signUpBtn.addEventListener('click', (e) => {{
                    e.preventDefault();
                    openClerkModal('signUp');
                }});

                // Listen for auth state changes
                clerkInstance.addListener(async (ev) => {{
                    console.log('[Clerk] Auth state changed:', ev);
                    if (ev.user && ev.session) {{
                        await handleAuthenticatedUser();
                    }}
                }});

                console.log('[Clerk] Event listeners attached');

            }} catch (err) {{
                console.error('[Clerk] Initialization error:', err);
                showError('Failed to initialize: ' + err.message);
                signInText.textContent = 'Error loading auth';
            }}
        }}

        function openClerkModal(mode) {{
            console.log(`[Clerk] Opening ${{mode}} modal`);

            if (!clerkInstance) {{
                showError('Auth not initialized');
                return;
            }}

            try {{
                const commonProps = {{
                    routing: 'virtual',
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
                }};

                if (mode === 'signIn') {{
                    clerkInstance.openSignIn({{
                        ...commonProps,
                        redirectUrl: CONFIG.dashboardUrl,
                        afterSignInUrl: CONFIG.dashboardUrl
                    }});
                }} else {{
                    clerkInstance.openSignUp({{
                        ...commonProps,
                        redirectUrl: CONFIG.onboardingUrl,
                        afterSignUpUrl: CONFIG.onboardingUrl
                    }});
                }}

                console.log(`[Clerk] ${{mode}} modal opened`);

            }} catch (err) {{
                console.error('[Clerk] Failed to open modal:', err);
                showError('Failed to open auth modal: ' + err.message);
            }}
        }}

        async function handleAuthenticatedUser() {{
            const authContainer = document.getElementById('auth-container');
            const loading = document.getElementById('loading');

            authContainer.classList.add('hidden');
            loading.classList.remove('hidden');

            try {{
                // Get JWT token from Clerk using the custom template
                const token = await clerkInstance.session.getToken({{ template: "fieldpulse-template" }});

                if (!token) {{
                    throw new Error('No token received from Clerk. Check JWT template configuration.');
                }}

                console.log('[Clerk] Got token, verifying with backend...');

                // Send token to Flask backend
                const response = await fetch(CONFIG.apiUrl, {{
                    method: 'POST',
                    headers: {{
                        'Content-Type': 'application/json',
                    }},
                    body: JSON.stringify({{ token: token }})
                }});

                const data = await response.json();
                console.log('[Clerk] Backend response:', response.status, data);

                if (response.ok && data.success) {{
                    console.log('[Clerk] Auth successful:', data);
                    // Redirect to dashboard or onboarding for new users
                    const redirectTo = data.redirect || CONFIG.dashboardUrl;
                    window.location.href = redirectTo;
                }} else {{
                    const errorMsg = data.error || 'Authentication failed';
                    throw new Error(errorMsg);
                }}

            }} catch (err) {{
                console.error('[Clerk] Auth error:', err);
                showError(err.message);
                loading.classList.add('hidden');
                authContainer.classList.remove('hidden');

                // Sign out if backend verification failed
                if (clerkInstance) {{
                    await clerkInstance.signOut();
                }}
            }}
        }}

        function showError(msg) {{
            console.error('[Clerk] Error:', msg);
            const errorDiv = document.getElementById('error');
            errorDiv.textContent = msg;
            errorDiv.classList.remove('hidden');
        }}

        // Initialize when DOM is ready
        if (document.readyState === 'loading') {{
            document.addEventListener('DOMContentLoaded', initClerk);
        }} else {{
            initClerk();
        }}
    </script>
</body>
</html>
"""


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
    return CLERK_LOGIN_TEMPLATE.format(
        clerk_pub_key=clerk_pub_key,
        app_domain=app_domain.rstrip('/'),
        tailwind_cdn=tailwind_cdn,
        custom_css=custom_css
    )
