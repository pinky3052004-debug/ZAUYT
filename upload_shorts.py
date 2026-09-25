import os
import io
import re
import json
from datetime import datetime, timedelta, timezone
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload, MediaFileUpload, MediaIoBaseUpload

# --- Google Drive Service ---
def get_gdrive_service():
    DRIVE_SCOPES = ['https://www.googleapis.com/auth/drive']
    creds_data = json.loads(os.environ['GDRIVE_CREDENTIALS'])
    creds = Credentials.from_authorized_user_info(creds_data, DRIVE_SCOPES)
    return build('drive', 'v3', credentials=creds)

# --- YouTube Service ---
def get_youtube_service():
    YOUTUBE_SCOPES = ['https://www.googleapis.com/auth/youtube.force-ssl']
    creds_data = json.loads(os.environ['YOUTUBE_CREDENTIALS'])
    creds = Credentials.from_authorized_user_info(creds_data, YOUTUBE_SCOPES)
    return build('youtube', 'v3', credentials=creds)

# --- uploaded.txt ကို စီမံခန့်ခွဲသည့် Function များ ---
def get_or_create_uploaded_file(drive_service, folder_id):
    """uploaded.txt ဖိုင်ကို ရှာဖွေပြီး ID ကိုပြန်ပေးသည်၊ မရှိပါက အသစ်ဆောက်သည်"""
    query = f"'{folder_id}' in parents and name='uploaded.txt' and trashed=false"
    results = drive_service.files().list(q=query, fields="files(id)").execute()
    files = results.get('files', [])
    
    if files:
        return files[0]['id']
    else:
        file_metadata = {
            'name': 'uploaded.txt',
            'mimeType': 'text/plain',
            'parents': [folder_id]
        }
        media = MediaIoBaseUpload(io.BytesIO(b""), mimetype='text/plain', resumable=True)
        new_file = drive_service.files().create(body=file_metadata, media_body=media, fields='id').execute()
        return new_file['id']

def get_uploaded_videos(drive_service, file_id):
    """uploaded.txt ထဲမှ တင်ပြီးသား ဗီဒီယိုနာမည်များကို List အဖြစ် ဖတ်ယူသည်"""
    try:
        request = drive_service.files().get_media(fileId=file_id)
        file_content = request.execute()
        return file_content.decode('utf-8').splitlines()
    except Exception:
        return []

def append_to_uploaded_file(drive_service, file_id, video_name, current_list):
    """uploaded.txt ထဲသို့ ဗီဒီယိုနာမည်အသစ်ကို လှမ်းထည့်ပြီး Drive ပေါ်တွင် Update လုပ်သည်"""
    current_list.append(video_name)
    new_content = "\n".join(current_list) + "\n"
    
    media = MediaIoBaseUpload(io.BytesIO(new_content.encode('utf-8')), mimetype='text/plain', resumable=True)
    drive_service.files().update(fileId=file_id, media_body=media).execute()

# --- Main Logic ---
def main():
    folder_id = os.environ.get('GDRIVE_FOLDER_ID')
    if not folder_id:
        print("Error: GDRIVE_FOLDER_ID ကို Environment Variable / Secrets တွင် မသတ်မှတ်ရသေးပါ။")
        return

    drive_service = get_gdrive_service()
    youtube_service = get_youtube_service()

    txt_file_id = get_or_create_uploaded_file(drive_service, folder_id)
    uploaded_videos_list = get_uploaded_videos(drive_service, txt_file_id)

    print("သတ်မှတ်ထားသော Folder ID အတွင်းမှ ဖိုင်များကို စစ်ဆေးနေသည်...")

    # ၁။ Drive ထဲမှ .mp4 ဖိုင်များအားလုံးကို Pagination သုံး၍ ရှာဖွေခြင်း
    items = []
    page_token = None
    query = f"'{folder_id}' in parents and mimeType='video/mp4' and trashed=false"
    
    while True:
        results = drive_service.files().list(
            q=query,
            fields="nextPageToken, files(id, name)",
            pageToken=page_token
        ).execute()
        items.extend(results.get('files', []))
        page_token = results.get('nextPageToken')
        if not page_token:
            break

    pending_videos = []
    for item in items:
        name = item['name']
        if name not in uploaded_videos_list:
            match = re.search(r'(\d+)', name)
            file_num = int(match.group(1)) if match else float('inf')
            pending_videos.append((file_num, item))

    # နံပါတ်စဉ်အလိုက် စီစဉ်ခြင်း (1.mp4, 2.mp4, ...)
    pending_videos.sort(key=lambda x: x[0])

    if not pending_videos:
        print("တင်ရန် ဗီဒီယိုအသစ် မတွေ့ရှိပါ။")
        return

    # တစ်ကြိမ်လျှင် အများဆုံး ၅ ဖိုင်
    videos_to_upload = pending_videos[:3]
    
    schedule_slots = [  
        (5, 00), 
        (14, 00),
        (16, 30)   
    ]

    mmt_tz = timezone(timedelta(hours=6, minutes=30))
    now_mmt = datetime.now(mmt_tz)
    
    # လက်ရှိရက်စွဲကို ဖော်ပြရန် (ဥပမာ - September 25, 2026 ပုံစံဖြင့်)
    current_date_str = now_mmt.strftime('%B %d, %Y')

    for index, (file_num, item) in enumerate(videos_to_upload):
        video_id = item['id']
        video_name = item['name']
        local_filename = f"temp_{video_name}"

        hour, minute = schedule_slots[index]
        slot_time = now_mmt.replace(hour=hour, minute=minute, second=0, microsecond=0)
        
        # 💡 အချိန်လွန်သွားပါက နောက်တစ်နေ့သို့ မပြောင်းတော့ဘဲ ထို ဗီဒီယိုကို မတင်ဘဲ Skip လုပ်ပါမည်
        if slot_time <= now_mmt:
            print(f"\n⚠️ [{index+1}/{len(videos_to_upload)}] Skip - {video_name} အတွက် MMT {hour:02d}:{minute:02d} အချိန်သည် လွန်သွားခဲ့ပြီဖြစ်၍ မတင်တော့ပါ။")
            continue

        utc_slot_time = slot_time.astimezone(timezone.utc)
        publish_at_iso = utc_slot_time.strftime('%Y-%m-%dT%H:%M:%SZ')

        print(f"\n[{index+1}/{len(videos_to_upload)}] ဒေါင်းလုဒ်ဆွဲနေသည်: {video_name}")

        try:
            # ၂။ Google Drive မှ Download ရယူခြင်း
            request = drive_service.files().get_media(fileId=video_id)
            with io.FileIO(local_filename, 'wb') as fh:
                downloader = MediaIoBaseDownload(fh, request)
                done = False
                while not done:
                    status, done = downloader.next_chunk()

            # Title မှာသာ လက်ရှိရက်စွဲကို ထည့်သွင်းမည် (Description နှင့် Tags လုံးဝမပါပါ)
            video_title = f"{current_date_str}"

            print(f"✨ Title: {video_title}")

            # ၃။ YouTube သို့ Upload တင်ခြင်း (Description နှင့် Tags ကို ဖြုတ်ထားသည်)
            print(f"YouTube တွင် Schedule သတ်မှတ်နေသည် - အချိန်: MMT {slot_time.strftime('%H:%M')} (UTC {publish_at_iso})")
            body = {
                'snippet': {
                    'title': video_title,
                    'categoryId': '24' # Entertainment
                },
                'status': {
                    'privacyStatus': 'private',
                    'publishAt': publish_at_iso,
                    'selfDeclaredMadeForKids': False
                }
            }

            media = MediaFileUpload(local_filename, chunksize=-1, resumable=True, mimetype='video/mp4')
            upload_request = youtube_service.videos().insert(
                part="snippet,status",
                body=body,
                media_body=media
            )

            response = None
            while response is None:
                status, response = upload_request.next_chunk()

            print(f"Schedule လုပ်ဆောင်ချက် အောင်မြင်သည်။ Video ID: {response['id']}")

            # ၄။ uploaded.txt တွင် မှတ်တမ်းတင်ခြင်း
            append_to_uploaded_file(drive_service, txt_file_id, video_name, uploaded_videos_list)
            print(f"uploaded.txt ထဲသို့ မှတ်တမ်းတင်ပြီးပါပြီ: {video_name}")

        except Exception as e:
            print(f"❌ Error ဖြစ်ပေါ်ခဲ့သည် ({video_name}): {e}")

        finally:
            # Local Temp ဖိုင်အား ရှင်းလင်းခြင်း
            if os.path.exists(local_filename):
                os.remove(local_filename)

if __name__ == '__main__':
    main()
