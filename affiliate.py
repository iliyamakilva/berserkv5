"""
منطق پاداش رفرال: فقط بعد از اولین خرید کاربرِ معرفی‌شده، و فقط یکبار،
و فقط اگه از فیلتر ضد تقلب رد بشه. مبلغ پاداش از تنظیمات قابل‌ویرایش
(`settings.ref_reward()`) خونده میشه، نه یه عدد ثابت در کد.
"""

import settings
from anti_fraud import flag_for_review, is_referral_suspicious
from db import add_balance, bump_daily, get_user, mark_rewarded


def reward_ref(user_id):
    """
    برمی‌گردونه (status, detail):
      ("rewarded", ref_id)      -> پاداش واریز شد
      ("skipped", reason)       -> رفرر نداشت / قبلا پاداش گرفته بود
      ("blocked", message)      -> ضد تقلب جلوش رو گرفت
    """
    row = get_user(user_id)
    if not row:
        return "skipped", "user_not_found"

    ref, rewarded = row["ref"], row["rewarded"]
    if not ref:
        return "skipped", "no_referrer"
    if rewarded:
        return "skipped", "already_rewarded"

    suspicious, reason = is_referral_suspicious(ref, user_id)
    if suspicious:
        return "blocked", flag_for_review(reason, ref, user_id)

    reward_amount = settings.ref_reward()
    add_balance(ref, reward_amount)
    mark_rewarded(user_id)
    bump_daily("referral_rewards", reward_amount)
    return "rewarded", ref
