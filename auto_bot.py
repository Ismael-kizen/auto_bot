# anon_bot_moderated.py
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder, MessageHandler, filters, CallbackQueryHandler, 
    CommandHandler, ContextTypes
)
import asyncio
import os
from datetime import datetime, timedelta
from collections import defaultdict
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

# Load configuration from environment variables
BOT_TOKEN = os.getenv("BOT_TOKEN", "")
CHANNEL_ID = os.getenv("CHANNEL_ID", "")
ADMINS_STR = os.getenv("ADMINS", "")
ADMINS = [int(admin_id.strip()) for admin_id in ADMINS_STR.split(",") if admin_id.strip()] if ADMINS_STR else []

# Validate required configuration
if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN environment variable is required. Please create a .env file with your bot token.")
if not CHANNEL_ID:
    raise ValueError("CHANNEL_ID environment variable is required. Please create a .env file with your channel ID.")
if not ADMINS:
    raise ValueError("ADMINS environment variable is required (comma-separated list of Telegram user IDs). Please create a .env file.")

# Configuration
MAX_QUEUE_SIZE = 50
RATE_LIMIT_COUNT = 3  # Max messages per user
RATE_LIMIT_WINDOW = 300  # 5 minutes in seconds

# In-memory queue for moderation
queue = {}
queue_lock = asyncio.Lock()
_next_qid = 1

# Rate limiting: track user submissions
user_submissions = defaultdict(list)  # user_id -> list of timestamps


def next_qid():
    global _next_qid
    qid = _next_qid
    _next_qid += 1
    return qid

def get_queue_position(qid):
    """Get position of item in queue (1-based)"""
    sorted_qids = sorted(queue.keys())
    try:
        return sorted_qids.index(qid) + 1
    except ValueError:
        return None

def check_rate_limit(user_id):
    """Check if user has exceeded rate limit. Returns (allowed, wait_seconds)"""
    now = datetime.now()
    user_submissions[user_id] = [
        ts for ts in user_submissions[user_id] 
        if now - ts < timedelta(seconds=RATE_LIMIT_WINDOW)
    ]
    
    if len(user_submissions[user_id]) >= RATE_LIMIT_COUNT:
        oldest = min(user_submissions[user_id])
        wait_until = oldest + timedelta(seconds=RATE_LIMIT_WINDOW)
        wait_seconds = int((wait_until - now).total_seconds())
        return False, wait_seconds
    return True, 0

def format_sender_info(user):
    """Format sender information for admin display"""
    name = user.first_name or "Unknown"
    username = f"@{user.username}" if user.username else "No username"
    user_id = user.id
    return f"👤 Sender: {name} ({username})\n🆔 User ID: {user_id}"

async def forward_media_to_admin(bot, admin_id, msg, qid, sender_info, keyboard=None):
    """Forward the actual media message to admin. Returns the sent message object."""
    try:
        caption_text = f"📷 Photo - Queue ID: {qid}\n\n{sender_info}\n\nCaption: {msg.caption or '(none)'}"
        if msg.photo:
            sent_msg = await bot.send_photo(
                chat_id=admin_id,
                photo=msg.photo[-1].file_id,
                caption=caption_text,
                parse_mode=None,
                reply_markup=keyboard
            )
            return sent_msg
        elif msg.video:
            caption_text = f"🎥 Video - Queue ID: {qid}\n\n{sender_info}\n\nCaption: {msg.caption or '(none)'}"
            sent_msg = await bot.send_video(
                chat_id=admin_id,
                video=msg.video.file_id,
                caption=caption_text,
                parse_mode=None,
                reply_markup=keyboard
            )
            return sent_msg
        elif msg.document:
            caption_text = f"📄 Document - Queue ID: {qid}\n\n{sender_info}\n\nCaption: {msg.caption or '(none)'}"
            sent_msg = await bot.send_document(
                chat_id=admin_id,
                document=msg.document.file_id,
                caption=caption_text,
                parse_mode=None,
                reply_markup=keyboard
            )
            return sent_msg
        elif msg.voice:
            caption_text = f"🎤 Voice - Queue ID: {qid}\n\n{sender_info}\n\nCaption: {msg.caption or '(none)'}"
            sent_msg = await bot.send_voice(
                chat_id=admin_id,
                voice=msg.voice.file_id,
                caption=caption_text,
                parse_mode=None,
                reply_markup=keyboard
            )
            return sent_msg
        return None
    except Exception as e:
        print(f"Error forwarding media: {e}")
        return None

# -----------------------------
# Handle /start
async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Send me text, media, or voice. It will go to moderation before posting anonymously."
    )

# -----------------------------
# Handle incoming messages
async def handle_private_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if msg.chat.type != "private":
        return

    user_id = msg.from_user.id
    
    # Check rate limit
    allowed, wait_seconds = check_rate_limit(user_id)
    if not allowed:
        minutes = wait_seconds // 60
        seconds = wait_seconds % 60
        await msg.reply_text(
            f"⏳ Rate limit exceeded. Please wait {minutes}m {seconds}s before submitting again.\n"
            f"You can submit up to {RATE_LIMIT_COUNT} messages every 5 minutes."
        )
        return

    # Check queue size
    async with queue_lock:
        if len(queue) >= MAX_QUEUE_SIZE:
            await msg.reply_text(
                f"⚠️ The moderation queue is full ({MAX_QUEUE_SIZE} items). Please try again later."
            )
            return

    # Check if admin is in edit mode
    if update.effective_user.id in ADMINS and context.user_data.get('editing_qid'):
        # Admin is editing, handle in edit handler (only if text message)
        if msg.text:
            await handle_edit_text(update, context)
        else:
            await msg.reply_text("⚠️ Please send text only when editing. Use /cancel to cancel editing.")
        return

    qid = next_qid()
    sender_info = format_sender_info(msg.from_user)
    
    entry = {
        "chat_id": msg.chat.id,
        "message_id": msg.message_id,
        "has_media": bool(msg.photo or msg.video or msg.document or msg.voice),
        "text": msg.text or "",
        "caption": msg.caption or "",
        "message_obj": msg,
        "sender_info": sender_info,
        "sender_id": user_id,
        "sender_name": msg.from_user.first_name or "Unknown",
        "sender_username": msg.from_user.username or None,
        "edited_text": None,  # Store edited text/caption
        "timestamp": datetime.now(),
        "admin_messages": {}  # Store admin notification message IDs: {admin_id: message_id}
    }

    async with queue_lock:
        queue[qid] = entry
        position = get_queue_position(qid)

    # Update rate limit
    user_submissions[user_id].append(datetime.now())

    await msg.reply_text(
        f"✅ Your message is in the moderation queue.\n"
        f"📊 Position: {position}/{len(queue)}"
    )

    # Send to admins
    preview = entry["text"] or entry["caption"] or ("<media>" if entry["has_media"] else "<empty>")
    if len(preview) > 300:
        preview = preview[:300] + "…"

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("Approve ✅", callback_data=f"approve:{qid}"),
         InlineKeyboardButton("Reject ❌", callback_data=f"reject:{qid}")],
        [InlineKeyboardButton("Edit ✏️", callback_data=f"edit:{qid}"),
         InlineKeyboardButton("View Details 👤", callback_data=f"details:{qid}")]
    ])

    for admin in ADMINS:
        try:
            # If it's a text message, send text with info
            if msg.text:
                admin_text = f"📝 New Text Submission — Queue ID: {qid}\n\n{sender_info}\n\n📄 Content:\n{msg.text}"
                sent_msg = await context.bot.send_message(
                    chat_id=admin,
                    text=admin_text,
                    reply_markup=keyboard
                )
                async with queue_lock:
                    if qid in queue:
                        queue[qid]["admin_messages"][admin] = sent_msg.message_id
            else:
                # For media, forward media with buttons attached
                media_msg = await forward_media_to_admin(context.bot, admin, msg, qid, sender_info, keyboard)
                # Store the message ID from the forwarded media (this is the main message with buttons)
                if media_msg:
                    async with queue_lock:
                        if qid in queue:
                            queue[qid]["admin_messages"][admin] = media_msg.message_id
        except Exception as e:
            print(f"Error sending to admin {admin}: {e}")

# Helper function to edit message text or caption based on message type
async def edit_admin_message(query, text, keyboard=None):
    """Edit message text or caption depending on message type"""
    msg = query.message
    try:
        # Check if it's a media message (has photo, video, document, or voice)
        if msg.photo or msg.video or msg.document or msg.voice:
            # Use edit_message_caption for media messages
            await query.edit_message_caption(caption=text, reply_markup=keyboard)
        else:
            # Use edit_message_text for text messages
            await query.edit_message_text(text=text, reply_markup=keyboard)
    except Exception as e:
        # Fallback: try edit_message_text if caption edit fails
        try:
            await query.edit_message_text(text=text, reply_markup=keyboard)
        except:
            # If both fail, send a new message
            await query.message.reply_text(text=text, reply_markup=keyboard)

# -----------------------------
# Handle approve/reject/edit/details buttons
async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    try:
        action, sid = query.data.split(":")
        qid = int(sid)
    except:
        await edit_admin_message(query, "❌ Bad callback data.")
        return

    if query.from_user.id not in ADMINS:
        await edit_admin_message(query, "❌ You are not authorized to moderate.")
        return

    async with queue_lock:
        entry = queue.get(qid, None)

    if not entry:
        await edit_admin_message(query, "❌ Submission not found or already handled.")
        return

    if action == "details":
        # Show detailed sender information
        edit_status = "✏️ (Edited)" if (entry.get("edited_text") or entry.get("edited_caption")) else ""
        details_text = (
            f"👤 Submission Details — Queue ID: {qid} {edit_status}\n\n"
            f"{entry['sender_info']}\n"
            f"📅 Submitted: {entry['timestamp'].strftime('%Y-%m-%d %H:%M:%S')}\n"
            f"📊 Queue Position: {get_queue_position(qid)}/{len(queue)}\n"
            f"📝 Type: {'Media' if entry['has_media'] else 'Text'}\n"
        )
        if entry['has_media']:
            if entry.get("edited_caption"):
                details_text += f"📄 Original Caption: {entry['caption'] or '(none)'}\n"
                details_text += f"✏️ Edited Caption: {entry['edited_caption']}\n"
            else:
                details_text += f"📄 Caption: {entry['caption'] or '(none)'}\n"
        else:
            if entry.get("edited_text"):
                details_text += f"📄 Original Text: {entry['text']}\n"
                details_text += f"✏️ Edited Text: {entry['edited_text']}\n"
            else:
                details_text += f"📄 Text: {entry['text']}\n"
        
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("Approve ✅", callback_data=f"approve:{qid}"),
             InlineKeyboardButton("Reject ❌", callback_data=f"reject:{qid}")],
            [InlineKeyboardButton("Edit ✏️", callback_data=f"edit:{qid}"),
             InlineKeyboardButton("← Back", callback_data=f"back:{qid}")]
        ])
        await edit_admin_message(query, details_text, keyboard)
        return

    if action == "back":
        # Return to main moderation view
        # Show edited content if available, otherwise show original
        if entry.get("edited_text"):
            preview = entry["edited_text"]
        elif entry.get("edited_caption"):
            preview = entry["edited_caption"]
        else:
            preview = entry["text"] or entry["caption"] or ("<media>" if entry["has_media"] else "<empty>")
        
        if len(preview) > 300:
            preview = preview[:300] + "…"
        
        edit_indicator = "✏️ Edited " if (entry.get("edited_text") or entry.get("edited_caption")) else ""
        admin_text = f"{edit_indicator}📝 Submission — Queue ID: {qid}\n\n{entry['sender_info']}\n\n📄 Content:\n{preview}"
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("Approve ✅", callback_data=f"approve:{qid}"),
             InlineKeyboardButton("Reject ❌", callback_data=f"reject:{qid}")],
            [InlineKeyboardButton("Edit ✏️", callback_data=f"edit:{qid}"),
             InlineKeyboardButton("View Details 👤", callback_data=f"details:{qid}")]
        ])
        await edit_admin_message(query, admin_text, keyboard)
        return

    if action == "edit":
        # Start edit conversation
        context.user_data['editing_qid'] = qid
        edit_prompt = (
            f"✏️ Editing Submission {qid}\n\n"
            f"Current {'caption' if entry['has_media'] else 'text'}:\n"
            f"{entry['caption'] if entry['has_media'] else entry['text'] or '(empty)'}\n\n"
            f"Please send the new {'caption' if entry['has_media'] else 'text'}.\n"
            f"Send /cancel to cancel editing."
        )
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("Cancel ❌", callback_data=f"cancel_edit:{qid}")]
        ])
        await edit_admin_message(query, edit_prompt, keyboard)
        return

    if action == "cancel_edit":
        context.user_data.pop('editing_qid', None)
        await edit_admin_message(query, "❌ Edit cancelled.")
        return

    if action == "reject":
        async with queue_lock:
            queue.pop(qid, None)
        await edit_admin_message(query, f"Submission {qid} rejected ❌")
        try:
            await context.bot.send_message(
                chat_id=entry["chat_id"],
                text="❌ Your submission was rejected by the moderator."
            )
        except:
            pass
        return

    if action == "approve":
        # Approve → post anonymously
        async with queue_lock:
            queue.pop(qid, None)
        
        try:
            msg = entry["message_obj"]
            # Use edited text if available, otherwise use original
            text_to_post = entry.get("edited_text") or entry["text"] or ""
            caption_to_post = entry.get("edited_caption") or entry["caption"] or ""
            
            if msg.text:
                await context.bot.send_message(chat_id=CHANNEL_ID, text=text_to_post)
            elif msg.photo:
                await context.bot.send_photo(
                    chat_id=CHANNEL_ID,
                    photo=msg.photo[-1].file_id,
                    caption=caption_to_post or None
                )
            elif msg.video:
                await context.bot.send_video(
                    chat_id=CHANNEL_ID,
                    video=msg.video.file_id,
                    caption=caption_to_post or None
                )
            elif msg.document:
                await context.bot.send_document(
                    chat_id=CHANNEL_ID,
                    document=msg.document.file_id,
                    caption=caption_to_post or None
                )
            elif msg.voice:
                await context.bot.send_voice(
                    chat_id=CHANNEL_ID,
                    voice=msg.voice.file_id,
                    caption=caption_to_post or None
                )

            await edit_admin_message(query, f"Submission {qid} approved ✅ and posted.")
            try:
                await context.bot.send_message(
                    chat_id=entry["chat_id"],
                    text="✅ Your submission was approved and posted anonymously."
                )
            except:
                pass
        except Exception as e:
            await edit_admin_message(query, f"❌ Error posting submission {qid}: {e}")

# -----------------------------
# Handle edit text input
async def handle_edit_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.chat.type != "private":
        return
    
    if update.effective_user.id not in ADMINS:
        await update.message.reply_text("❌ Not authorized.")
        return

    qid = context.user_data.get('editing_qid')
    if not qid:
        return

    admin_id = update.effective_user.id
    async with queue_lock:
        entry = queue.get(qid, None)
        if not entry:
            await update.message.reply_text("❌ Submission not found.")
            context.user_data.pop('editing_qid', None)
            return

        new_text = update.message.text or ""
        
        if entry['has_media']:
            entry['edited_caption'] = new_text
            # Don't update original caption, keep it for reference
        else:
            entry['edited_text'] = new_text
            # Don't update original text, keep it for reference

    context.user_data.pop('editing_qid', None)
    
    # Update the original admin notification message with edited content and buttons
    admin_message_id = entry.get("admin_messages", {}).get(admin_id)
    
    # Prepare updated content display
    if entry['has_media']:
        content_display = f"📄 Caption: {new_text or '(empty)'}"
        submission_type = "Media"
    else:
        content_display = f"📄 Content:\n{new_text}"
        submission_type = "Text"
    
    updated_text = (
        f"✏️ Edited {submission_type} Submission — Queue ID: {qid}\n\n"
        f"{entry['sender_info']}\n\n"
        f"{content_display}\n\n"
        f"✅ Edit completed. Review and approve or reject."
    )
    
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("Approve ✅", callback_data=f"approve:{qid}"),
         InlineKeyboardButton("Reject ❌", callback_data=f"reject:{qid}")],
        [InlineKeyboardButton("Edit ✏️", callback_data=f"edit:{qid}"),
         InlineKeyboardButton("View Details 👤", callback_data=f"details:{qid}")]
    ])
    
    try:
        if admin_message_id:
            # Update the original message - use caption for media, text for text messages
            if entry['has_media']:
                await context.bot.edit_message_caption(
                    chat_id=admin_id,
                    message_id=admin_message_id,
                    caption=updated_text,
                    reply_markup=keyboard
                )
            else:
                await context.bot.edit_message_text(
                    chat_id=admin_id,
                    message_id=admin_message_id,
                    text=updated_text,
                    reply_markup=keyboard
                )
            await update.message.reply_text(f"✅ Updated! Check the moderation message above.")
        else:
            # Fallback: send new message if we can't find the original
            await context.bot.send_message(
                chat_id=admin_id,
                text=updated_text,
                reply_markup=keyboard
            )
            await update.message.reply_text(f"✅ {'Caption' if entry['has_media'] else 'Text'} updated for submission {qid}.")
    except Exception as e:
        # If update fails, send a new message
        await context.bot.send_message(
            chat_id=admin_id,
            text=updated_text,
            reply_markup=keyboard
        )
        await update.message.reply_text(f"✅ {'Caption' if entry['has_media'] else 'Text'} updated for submission {qid}.")

async def cancel_edit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.pop('editing_qid', None)
    await update.message.reply_text("❌ Edit cancelled.")

# -----------------------------
# Optional: list pending queue
async def queue_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMINS:
        await update.message.reply_text("❌ Not allowed.")
        return
    async with queue_lock:
        if not queue:
            await update.message.reply_text("No pending submissions.")
            return
        lines = []
        for qid, e in sorted(queue.items()):
            preview = (e["text"] or e["caption"] or "")[:50] + ("…" if len(e["text"] or e["caption"] or "") > 50 else "")
            sender = e.get("sender_name", "Unknown")
            lines.append(f"{qid}: {sender} — {'media' if e['has_media'] else 'text'}; {preview}")
        stats = f"📊 Queue Statistics:\nTotal: {len(queue)}/{MAX_QUEUE_SIZE}\n\n"
    await update.message.reply_text(stats + "Pending submissions:\n" + "\n".join(lines))

# -----------------------------
# Main
def main():
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    
    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(CommandHandler("queue", queue_cmd))
    app.add_handler(CommandHandler("cancel", cancel_edit))
    app.add_handler(MessageHandler(filters.ChatType.PRIVATE, handle_private_message))
    app.add_handler(CallbackQueryHandler(callback_handler))
    
    print("🤖 Moderated anonymous bot running...")
    print(f"📊 Rate limit: {RATE_LIMIT_COUNT} messages per {RATE_LIMIT_WINDOW//60} minutes")
    print(f"📋 Max queue size: {MAX_QUEUE_SIZE}")
    app.run_polling()

if __name__ == "__main__":
    main()
