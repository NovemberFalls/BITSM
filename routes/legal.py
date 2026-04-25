"""Legal pages: Privacy Policy, Terms of Service, Data Processing Agreement.

These routes are intentionally unauthenticated — legal documents must be
accessible to anyone before they create an account or accept terms.
"""

from flask import Blueprint, render_template
from config import Config

legal_bp = Blueprint("legal", __name__)

_COMPANY = Config.LEGAL_COMPANY_NAME
_PRODUCT = Config.APP_NAME
_CONTACT_EMAIL = Config.LEGAL_CONTACT_EMAIL
_APP_URL = Config.APP_URL


@legal_bp.route("/legal/privacy")
def privacy_policy():
    return render_template(
        "legal/privacy_policy.html",
        company=_COMPANY,
        product=_PRODUCT,
        contact_email=_CONTACT_EMAIL,
        app_url=_APP_URL,
    )


@legal_bp.route("/legal/terms")
def terms_of_service():
    return render_template(
        "legal/terms_of_service.html",
        company=_COMPANY,
        product=_PRODUCT,
        contact_email=_CONTACT_EMAIL,
        app_url=_APP_URL,
    )


@legal_bp.route("/legal/dpa")
def data_processing_agreement():
    return render_template(
        "legal/data_processing_agreement.html",
        company=_COMPANY,
        product=_PRODUCT,
        contact_email=_CONTACT_EMAIL,
        app_url=_APP_URL,
    )
