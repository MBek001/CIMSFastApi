import requests

def get_instagram_followers_count(access_token, instagram_business_id):
    """
    Instagram Business Account uchun followerlar sonini olish.
    """
    url = f"https://graph.facebook.com/v21.0/{instagram_business_id}"
    params = {
        "fields": "followers_count",
        "access_token": access_token
    }

    response = requests.get(url, params=params)
    if response.status_code == 200:
        data = response.json()
        followers = data.get("followers_count", "N/A")
        print(f"👥 Followerlar soni: {followers}")
        return followers
    else:
        print("❌ Xato:", response.text)
        return None


if __name__ == "__main__":
    # 🔑 Bu yerga o‘z token va Instagram Business ID’ni yozing
    ACCESS_TOKEN = "EAAMRtC0qc2sBQNCP7FaZCcNPoC0fiDn0mxK6nnZAbVJ5hwmfEuIps3fOTG8giENhRusmykCTppgz9fAGfZAbFqZAfusAKQkOEkVRL11hmHGcnXY1UZBpsmDdqQHF9FVRTDm5IX51q5H2hTaonZC4lKd6EwrYCrHTZAzgrBAppakDDUK6OLhHKdnfoMSvhP6n7PzMpITxtjDlVL1bWTohfvVQrF2ZAb8kn2SGPnMoPOf6jcGvPn8GySYYbtT7eV5RNZAraIrgnBZAZCWRdQTfp0v"   # Meta Access Token
    INSTAGRAM_BUSINESS_ID = "17841476392326035"  # IG Business ID

    get_instagram_followers_count(ACCESS_TOKEN, INSTAGRAM_BUSINESS_ID)
