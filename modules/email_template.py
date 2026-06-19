"""
email_template.py — HTML email templates for Piney Digital
Piney Digital Outreach System

Professional HTML email templates with brand colors and CTA buttons.
"""

# Piney Digital Brand Colors (from website)
BRAND_COLORS = {
    "primary": "#1e5a8e",
    "primary_dark": "#14405f",
    "primary_light": "#2874b5",
    "secondary": "#2d7f4e",
    "secondary_dark": "#1f5836",
    "secondary_light": "#3a9c5f",
    "accent": "#ff8c42",
    "accent_dark": "#e67a33",
    "white": "#ffffff",
    "gray_700": "#374151",
    "gray_600": "#4b5563",
    "gray_500": "#6b7280",
    "gray_100": "#f3f4f6",
}


def get_initial_email_html(
    business_name: str,
    city: str,
    body_paragraphs: list,
    cta_url: str = "https://pineydigital.com",
    cta_text: str = "Learn More",
    campaign: str = "enterprise_outreach",
    email_type: str = "initial",
) -> str:
    """
    Generate HTML for initial outreach email with UTM tracking.

    Args:
        business_name: Name of the recipient's business
        city: City of the business
        body_paragraphs: List of paragraph strings
        cta_url: URL for the CTA button
        cta_text: Text for the CTA button
        campaign: Campaign name for tracking (default: enterprise_outreach)
        email_type: Type of email (initial, follow_up_1, follow_up_2, breakup)

    Returns:
        HTML string
    """
    # Generate UTM parameters for tracking
    # Sanitize business name for URL (remove special chars, replace spaces with underscores)
    business_slug = business_name.lower().replace(" ", "_").replace("'", "").replace("-", "_")[:30]
    business_slug = "".join(c for c in business_slug if c.isalnum() or c == "_")

    # Build URL with UTM parameters
    utm_params = f"utm_source=email&utm_medium=outreach&utm_campaign={campaign}&utm_content={business_slug}_{email_type}"

    # Add UTM to URL (handle existing query params)
    if "?" in cta_url:
        cta_url_with_utm = f"{cta_url}&{utm_params}"
    else:
        cta_url_with_utm = f"{cta_url}?{utm_params}"
    """
    Generate HTML for initial outreach email.

    Args:
        business_name: Name of the recipient's business
        city: City of the business
        body_paragraphs: List of paragraph strings
        cta_url: URL for the CTA button
        cta_text: Text for the CTA button

    Returns:
        HTML string
    """
    paragraphs_html = "\n".join(f"        <p style=\"margin: 0 0 16px 0; color: {BRAND_COLORS['gray_700']}; font-size: 16px; line-height: 1.6;\">{p}</p>" for p in body_paragraphs)

    return f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Message from Piney Digital</title>
</head>
<body style="margin: 0; padding: 0; background-color: {BRAND_COLORS['gray_100']}; font-family: 'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;">
    <!-- Email Container -->
    <table role="presentation" cellspacing="0" cellpadding="0" border="0" width="100%" style="max-width: 600px; margin: 0 auto; background-color: {BRAND_COLORS['white']};">

        <!-- Header with Gradient -->
        <tr>
            <td style="padding: 40px 40px 32px 40px; text-align: center; background: linear-gradient(135deg, {BRAND_COLORS['primary']} 0%, {BRAND_COLORS['secondary']} 100%);">
                <!-- Logo -->
                <table role="presentation" cellspacing="0" cellpadding="0" border="0" style="margin: 0 auto;">
                    <tr>
                        <td style="font-size: 32px; padding-right: 12px;">🌲</td>
                        <td style="text-align: left;">
                            <h1 style="margin: 0; font-family: 'Poppins', sans-serif; font-size: 24px; font-weight: 700; color: {BRAND_COLORS['white']};">
                                Piney Digital
                            </h1>
                            <p style="margin: 4px 0 0 0; font-size: 12px; color: rgba(255,255,255,0.8);">
                                Custom Platforms for Growing Businesses
                            </p>
                        </td>
                    </tr>
                </table>
            </td>
        </tr>

        <!-- Greeting -->
        <tr>
            <td style="padding: 32px 40px 0 40px;">
                <p style="margin: 0; font-size: 18px; color: {BRAND_COLORS['gray_700']};">
                    Hi {business_name} team,
                </p>
            </td>
        </tr>

        <!-- Body -->
        <tr>
            <td style="padding: 20px 40px 32px 40px;">
{paragraphs_html}
            </td>
        </tr>

        <!-- CTA Button -->
        <tr>
            <td style="padding: 0 40px 32px 40px; text-align: center;">
                <table role="presentation" cellspacing="0" cellpadding="0" border="0" style="margin: 0 auto;">
                    <tr>
                        <td style="border-radius: 8px; background: linear-gradient(135deg, {BRAND_COLORS['primary']} 0%, {BRAND_COLORS['primary_light']} 100%); box-shadow: 0 4px 6px rgba(30, 90, 142, 0.3);">
                            <a href="{cta_url_with_utm}" target="_blank" style="display: inline-block; padding: 16px 32px; font-family: 'Poppins', sans-serif; font-size: 16px; font-weight: 600; color: {BRAND_COLORS['white']}; text-decoration: none;">
                                {cta_text} →
                            </a>
                        </td>
                    </tr>
                </table>
            </td>
        </tr>

        <!-- Signature -->
        <tr>
            <td style="padding: 24px 40px; background-color: {BRAND_COLORS['gray_100']};">
                <table role="presentation" cellspacing="0" cellpadding="0" border="0" width="100%">
                    <tr>
                        <td>
                            <p style="margin: 0 0 8px 0; font-size: 14px; color: {BRAND_COLORS['gray_500']};">
                                Best regards,
                            </p>
                            <p style="margin: 0 0 4px 0; font-size: 18px; font-weight: 600; color: {BRAND_COLORS['gray_700']};">
                                Joel Escoto
                            </p>
                            <p style="margin: 0; font-size: 14px;">
                                <a href="{cta_url_with_utm}" style="color: {BRAND_COLORS['primary']}; text-decoration: none; font-weight: 500;">pineydigital.com</a>
                            </p>
                        </td>
                        <td style="text-align: right; vertical-align: middle;">
                            <span style="font-size: 28px;">🌲</span>
                        </td>
                    </tr>
                </table>
            </td>
        </tr>

        <!-- Footer -->
        <tr>
            <td style="padding: 20px 40px; text-align: center; border-top: 1px solid {BRAND_COLORS['gray_100']};">
                <p style="margin: 0 0 8px 0; font-size: 12px; color: {BRAND_COLORS['gray_500']};">
                    Piney Digital · East Texas
                </p>
                <p style="margin: 0; font-size: 11px; color: {BRAND_COLORS['gray_500']};">
                    You received this email because we thought our services might be relevant to your business.
                </p>
            </td>
        </tr>

    </table>
</body>
</html>"""


def get_followup_email_html(
    business_name: str,
    body_paragraphs: list,
    cta_url: str = "https://pineydigital.com",
    cta_text: str = "Schedule a Call",
    previous_email_date: str = None,
) -> str:
    """
    Generate HTML for follow-up email.
    """
    paragraphs_html = "\n".join(f"        <p style=\"margin: 0 0 16px 0; color: {BRAND_COLORS['gray_700']}; font-size: 16px; line-height: 1.6;\">{p}</p>" for p in body_paragraphs)

    followup_header = f"<p style='margin: 0 0 16px 0; font-size: 14px; color: {BRAND_COLORS['gray_600']};'>Following up on my email from {previous_email_date}.</p>" if previous_email_date else ""

    return f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Following Up - Piney Digital</title>
</head>
<body style="margin: 0; padding: 0; background-color: {BRAND_COLORS['gray_100']}; font-family: 'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;">
    <table role="presentation" cellspacing="0" cellpadding="0" border="0" width="100%" style="max-width: 600px; margin: 0 auto; background-color: {BRAND_COLORS['white']};">

        <!-- Header -->
        <tr>
            <td style="padding: 32px 40px 24px 40px; text-align: center; border-bottom: 3px solid {BRAND_COLORS['secondary']};">
                <h1 style="margin: 0; font-family: 'Poppins', sans-serif; font-size: 24px; font-weight: 700; color: {BRAND_COLORS['primary']};">
                    Piney Digital
                </h1>
            </td>
        </tr>

        <!-- Greeting -->
        <tr>
            <td style="padding: 24px 40px 0 40px;">
                {followup_header}
                <p style="margin: 0; font-size: 18px; color: {BRAND_COLORS['gray_700']};">
                    Hi {business_name} team,
                </p>
            </td>
        </tr>

        <!-- Body -->
        <tr>
            <td style="padding: 20px 40px 32px 40px;">
{paragraphs_html}
            </td>
        </tr>

        <!-- CTA Button -->
        <tr>
            <td style="padding: 0 40px 32px 40px; text-align: center;">
                <table role="presentation" cellspacing="0" cellpadding="0" border="0" style="margin: 0 auto;">
                    <tr>
                        <td style="background-color: {BRAND_COLORS['secondary']}; border-radius: 8px;">
                            <a href="{cta_url}" target="_blank" style="display: inline-block; padding: 16px 32px; font-family: 'Poppins', sans-serif; font-size: 16px; font-weight: 600; color: {BRAND_COLORS['white']}; text-decoration: none;">
                                {cta_text} →
                            </a>
                        </td>
                    </tr>
                </table>
            </td>
        </tr>

        <!-- Signature -->
        <tr>
            <td style="padding: 0 40px 32px 40px;">
                <p style="margin: 0 0 8px 0; font-size: 16px; color: {BRAND_COLORS['gray_700']};">
                    Best,<br>
                    <strong>Joel Escoto</strong>
                </p>
                <p style="margin: 0; font-size: 14px; color: {BRAND_COLORS['primary']};">
                    <a href="https://pineydigital.com" style="color: {BRAND_COLORS['primary']}; text-decoration: none;">pineydigital.com</a>
                </p>
            </td>
        </tr>

    </table>
</body>
</html>"""


def get_breakup_email_html(
    business_name: str,
) -> str:
    """
    Generate HTML for breakup email (last touch).
    """
    return f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Quick Note - Piney Digital</title>
</head>
<body style="margin: 0; padding: 0; background-color: {BRAND_COLORS['gray_100']}; font-family: 'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;">
    <table role="presentation" cellspacing="0" cellpadding="0" border="0" width="100%" style="max-width: 600px; margin: 0 auto; background-color: {BRAND_COLORS['white']};">

        <!-- Header -->
        <tr>
            <td style="padding: 32px 40px 24px 40px; text-align: center;">
                <h1 style="margin: 0; font-family: 'Poppins', sans-serif; font-size: 24px; font-weight: 700; color: {BRAND_COLORS['primary']};">
                    Piney Digital
                </h1>
            </td>
        </tr>

        <!-- Body -->
        <tr>
            <td style="padding: 20px 40px 32px 40px;">
                <p style="margin: 0 0 16px 0; font-size: 18px; color: {BRAND_COLORS['gray_700']};">
                    Hi {business_name} team,
                </p>
                <p style="margin: 0 0 16px 0; font-size: 16px; color: {BRAND_COLORS['gray_700']}; line-height: 1.6;">
                    I'll take you off my outreach list for now. If you ever need help with your website or digital systems, feel free to reach out.
                </p>
                <p style="margin: 0; font-size: 16px; color: {BRAND_COLORS['gray_700']};">
                    Wishing you the best with {business_name}.
                </p>
            </td>
        </tr>

        <!-- Signature -->
        <tr>
            <td style="padding: 0 40px 32px 40px;">
                <p style="margin: 0 0 4px 0; font-size: 16px; font-weight: 600; color: {BRAND_COLORS['gray_700']};">
                    Joel Escoto
                </p>
                <p style="margin: 0; font-size: 14px; color: {BRAND_COLORS['primary']};">
                    <a href="https://pineydigital.com" style="color: {BRAND_COLORS['primary']}; text-decoration: none;">pineydigital.com</a>
                </p>
            </td>
        </tr>

    </table>
</body>
</html>"""


def text_to_paragraphs(body_text: str) -> list:
    """
    Split body text into paragraphs for HTML formatting.

    Args:
        body_text: Plain text body

    Returns:
        List of paragraph strings
    """
    # Split on double newlines
    paragraphs = [p.strip() for p in body_text.split("\n\n") if p.strip()]
    if not paragraphs:
        paragraphs = [body_text]
    return paragraphs