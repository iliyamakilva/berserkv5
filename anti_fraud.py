from config import MAX_REFERRALS_PER_DAY, MAX_TOTAL_REFERRALS
from db import get_user, referral_count, referrals_rewarded_today


def is_referral_suspicious(ref_id, referred_id):
    ref_id, referred_id = str(ref_id), str(referred_id)
    if ref_id == referred_id:
        return True, "self_referral"
    referrer = get_user(ref_id)
    if not referrer:
        return True, "referrer_not_found"
    if referrer["banned"]:
        return True, "referrer_banned"
    referred = get_user(referred_id)
    if referred and referred["banned"]:
        return True, "referred_banned"
    if referral_count(ref_id) > MAX_TOTAL_REFERRALS:
        return True, "referral_cap_exceeded"
    if referrals_rewarded_today(ref_id) >= MAX_REFERRALS_PER_DAY:
        return True, "daily_referral_limit"
    return False, None


def flag_for_review(reason, ref_id, referred_id):
    return f"⚠️ پاداش رفرال بلاک شد ({reason})\nمعرف: {ref_id}\nمعرفی‌شده: {referred_id}"
