# AI Movie Recap Studio

ရုပ်ရှင်ဗီဒီယိုကို Recap / Dubbing / Subtitle only အဖြစ် အလိုအလျောက် ပြုလုပ်ပေးသည့် ကိရိယာ။

## Folder ဖွဲ့စည်းပုံ

```
movie-recap/
├─ main.py                  FastAPI app (web server စတင်ရာ)  →  uvicorn main:app
├─ run.bat                  Windows မှာ double-click နှိပ်၍ server စတင်ရန်
├─ requirements.txt         pip install -r requirements.txt
├─ requirements-local.txt   (ကိုယ့်စက်မှာ Whisper run လိုမှ) faster-whisper
├─ .env                     ကိုယ်ပိုင် key / ဆက်တင် (.env.example ကို ကူးပြီး ဖန်တီးပါ၊ GitHub မတင်ပါနှင့်)
│
├─ app/                     Python source အားလုံး
│  ├─ pipeline.py           ဗီဒီယိုထုတ်လုပ်ရေး အဓိက လုပ်ငန်းစဉ်
│  ├─ api/                  deps.py (login/CSRF စစ်) · auth_routes.py · video_routes.py (ဗီဒီယိုစာရင်း/Cancel/ဖိုင်ကာကွယ်ရေး)
│  ├─ core/                 config · jobs (queue/cancel) · services (FFmpeg) · db (SQLite) · auth · security · mailer · captcha · videos · cleanup
│  ├─ ai/                   gemini_service · llm_fallback (Claude/OpenAI အရန်) · transcribe · tts_engines · text_cleaner
│  └─ media/                video_edit · shapes · intro_cover (Poster/Cover) · tts_studio
│
├─ static/                  Web UI
│  ├─ index.html            app ကိုယ်ထည် (sidebar + ဗီဒီယိုပြုလုပ်ရန်)    auth.html  Login/Register/Reset
│  ├─ css/style.css
│  └─ js/                   utils · source-voice · style-edit · preview · generate · shell (sidebar) · auth
│
├─ fonts/                   .ttf / .otf font ထည့်ရန်
├─ tools/                   fix_tts_retry.py
│
└─ data/ outputs/ jobs/ cache/   run ချိန်မှာ အလိုအလျောက်ဖန်တီးသည်
                                 (data = SQLite အကောင့်စာရင်း၊ outputs/<user_id>/ = ထုတ်ပြီးဖိုင်၊ ၄၈ နာရီပြည့်ဖျက်)
```

## စတင်ရန်

1. `py -3.12 -m venv venv` ပြီး `venv\Scripts\activate`
2. `pip install -r requirements.txt`   (ကိုယ့်စက်မှာ Whisper run မည်ဆိုလျှင် `-r requirements-local.txt` ပါ ထပ်ထည့်)
3. `.env.example` ကို `.env` အဖြစ်ကူး၍ `GEMINI_API_KEY` ထည့်ပါ
4. `run.bat` ကို double-click (သို့) `uvicorn main:app --host 127.0.0.1 --port 8000`
5. Browser မှာ http://127.0.0.1:8000

## Speech-to-Text (စကား → စာသား)

`.env` ထဲက `TRANSCRIBE_BACKEND` ဖြင့် ရွေးပါ။

- `local` (မူလ) - faster-whisper ကို ကိုယ့်စက်မှာ run (GPU/CPU)
- `groq` / `openai` - API သုံး၊ GPU မလို။ `GROQ_API_KEY` သို့မဟုတ် `OPENAI_API_KEY` ထည့်ပါ။

## အကောင့်နှင့် လုံခြုံရေး

- Email + Password ဖြင့် အကောင့်ဖွင့်၊ **Email အတည်ပြု link** နှိပ်မှ Login ဝင်နိုင်သည်။
- **Password မေ့လျှင်** email ဖြင့် reset link (၁ နာရီ၊ တစ်ကြိမ်သာသုံးနိုင်)။
- **Google reCAPTCHA v2** (`RECAPTCHA_SITE_KEY` / `RECAPTCHA_SECRET` ထည့်လျှင် register / forgot / resend နှင့် Login မှားများလျှင် တောင်းသည်)။
- **Google ဖြင့် ဝင်ရန် ("Continue with Google")** - `.env` ထဲ `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET` / `SESSION_SECRET` ထည့်လျှင် ခလုတ်ပေါ်လာသည် (အသေးစိတ် အောက်တွင်)။
- Password ကို scrypt ဖြင့် hash ၊ session cookie သည် HttpOnly + SameSite=Lax ၊ Login/Reset ကြိုးစားမှုများကို ကန့်သတ်။
- ဗီဒီယိုဖိုင်များကို ပိုင်ရှင်သာ ကြည့်/ဒေါင်းလုဒ်လုပ်နိုင် (`/outputs/<user_id>/...`)။
- Email ပို့ရန် `SMTP_*` သတ်မှတ်ပါ။ မသတ်မှတ်လျှင် link များကို server terminal မှာ ပြသည် (ကိုယ့်စက်မှာ စမ်းရန်)။
- Online server တင်လျှင်: HTTPS သုံး၊ `.env` မှာ `COOKIE_SECURE=1` ၊ `APP_BASE_URL=https://...` ၊ Nginx နောက်ကဆိုလျှင် `TRUST_PROXY=1`။

## Cancel နှင့် ဖိုင်အလိုအလျောက်ဖျက်ခြင်း

- ဗီဒီယိုထုတ်နေစဉ် **Cancel** ခလုတ်ကို နှိပ်နိုင်သည် (တန်းစီနေလျှင် ချက်ချင်း၊ run နေလျှင် လက်ရှိအဆင့်ပြီးသည်နှင့် ရပ်)။
- ထုတ်ပြီးဗီဒီယို၊ ယာယီဖိုင်များကို `RETENTION_HOURS` (မူလ ၄၈ နာရီ) ပြည့်လျှင် ၃၀ မိနစ်တစ်ကြိမ် အလိုအလျောက်ရှင်းလင်းသည်။

## Google Login ("Continue with Google")

1. Google Cloud Console > APIs & Services > Credentials > **OAuth client ID (Web application)** ဖန်တီးပါ။
2. **Authorized redirect URI** ကို `APP_BASE_URL` + `/api/auth/google/callback` အတိုင်း တစ်လုံးမကွာ ထည့်ပါ (ဥပမာ `http://127.0.0.1:8000/api/auth/google/callback`)။
3. `.env` ထဲ ထည့်ပါ:
   ```
   GOOGLE_CLIENT_ID=xxxxxxxx.apps.googleusercontent.com
   GOOGLE_CLIENT_SECRET=GOCSPX-xxxxxxxx
   SESSION_SECRET=ရှည်ရှည် random စာကြောင်း   (python -c "import secrets; print(secrets.token_urlsafe(48))")
   ```
4. `pip install -r requirements.txt` ပြန်run ပြီး server ကို restart လုပ်ပါ။

- Google email တူသော အကောင့်ရှိပြီးသားဆိုလျှင် အလိုအလျောက် ချိတ်ပေးသည်။ မရှိလျှင် အကောင့်အသစ် ဖန်တီးပေးသည် (Google က email အတည်ပြုပြီးသားဖြစ်မှသာ)။
- Google ဖြင့်ဖွင့်သော အကောင့်တွင် Password မရှိ။ နောက်ပိုင်း Password သုံးလိုလျှင် "Password မေ့နေသလား" ဖြင့် သတ်မှတ်နိုင်သည်။
- ရှိပြီးသား `data/app.db` ကို server စတင်ချိန်တွင် column အသစ်များ အလိုအလျောက်ထည့်ပေးသည် (ဒေတာမဆုံးရှုံး)။
- Nginx နောက်ကဆိုလျှင် `SESSION_SECRET` ကို ပုံသေထားပြီး uvicorn ကို `--proxy-headers` ဖြင့် run ပါ။
