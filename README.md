# Telegram Anonymous Moderated Bot

A Telegram bot that allows users to submit messages (text, photos, videos, documents, and voice messages) for anonymous posting to a channel after admin moderation.

## Features

- ✅ **Content Moderation**: All submissions go through an admin review queue before posting
- 🎤 **Media Support**: Supports text, photos, videos, documents, and voice messages
- ✏️ **Edit Before Posting**: Admins can edit text/captions before approving
- 👤 **Sender Information**: Admins can view sender details (username, user ID, name)
- 🛡️ **Spam Protection**: Rate limiting (3 messages per 5 minutes per user)
- 📊 **Queue Management**: Queue limit (50 pending items) and position tracking
- 🔒 **Admin Only**: Only authorized admins can moderate submissions

## Setup

### Prerequisites

- Python 3.8 or higher
- A Telegram bot token (get one from [@BotFather](https://t.me/BotFather))
- A Telegram channel where approved messages will be posted
- Your Telegram user ID (get it from [@userinfobot](https://t.me/userinfobot))

### Installation

1. Clone this repository:
```bash
git clone <your-repo-url>
cd Auto_bot
```

2. Create a virtual environment:
```bash
python3 -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate
```

3. Install dependencies:
```bash
pip install -r requirements.txt
```

4. Configure the bot:
```bash
cp .env.example .env
```

5. Edit `.env` and add your configuration:
```env
BOT_TOKEN=your_bot_token_here
CHANNEL_ID=@your_channel_username
ADMINS=123456789,987654321
```

### Running the Bot

```bash
source venv/bin/activate  # On Windows: venv\Scripts\activate
python auto_bot.py
```

For production, you can run it in the background:
```bash
nohup python auto_bot.py > bot.log 2>&1 &
```

## Configuration

You can customize the following settings in `auto_bot.py`:

- `MAX_QUEUE_SIZE`: Maximum number of pending submissions (default: 50)
- `RATE_LIMIT_COUNT`: Number of messages allowed per time window (default: 3)
- `RATE_LIMIT_WINDOW`: Time window in seconds (default: 300 = 5 minutes)

## Usage

### For Users

1. Start a chat with your bot
2. Send any message (text, photo, video, document, or voice)
3. Wait for admin approval
4. Your message will be posted anonymously to the channel if approved

### For Admins

When a submission is received, you'll get a notification with:
- Sender information (name, username, user ID)
- The content (or media preview)
- Action buttons:
  - **Approve ✅**: Post the message to the channel
  - **Reject ❌**: Reject the submission
  - **Edit ✏️**: Edit the text/caption before approving
  - **View Details 👤**: See full submission details

#### Commands

- `/start` - Get started with the bot
- `/queue` - View pending submissions (admin only)

## How It Works

1. User sends a message to the bot
2. Message is added to the moderation queue
3. Admin receives notification with sender info and content
4. Admin can approve, reject, or edit the submission
5. If approved, message is posted anonymously to the channel
6. User receives notification about the status

## Security Notes

- Never commit your `.env` file to version control
- Keep your bot token secure
- Only add trusted users to the ADMINS list
- The bot only accepts private messages (not group messages)

## License

This project is open source and available under the MIT License.

## Contributing

Contributions are welcome! Please feel free to submit a Pull Request.

## Support

If you encounter any issues or have questions, please open an issue on GitHub.

