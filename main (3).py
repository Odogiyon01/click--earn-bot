
import sqlite3
import os
import time
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes, MessageHandler, filters
from flask import Flask, render_template, request, redirect, jsonify
import threading
from datetime import datetime
import asyncio
import signal
import sys

# === CONFIGURATION ===
BOT_TOKEN = "7851725524:AAEj57nHB1nIAsH7uaH9_ulPj5RPBXodgy4"
ADMIN_ID = 6723786276 # Replace with your Telegram numeric ID
ADMAVEN_LINK = "https://free-content.pro/s?EgDDp5li"
MIN_WITHDRAW = 1.00
CLICK_REWARD = 0.01
REFERRAL_REWARD = 0.05
DB_NAME = "clickbot.db"

# === INIT DATABASE ===
def init_db():
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY,
            balance REAL DEFAULT 0,
            clicks INTEGER DEFAULT 0,
            referrer INTEGER,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS withdrawals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            method TEXT,
            amount REAL,
            status TEXT DEFAULT 'pending',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS click_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            clicked_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS link_clicks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            ip_address TEXT,
            user_agent TEXT,
            clicked_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            referrer TEXT
        )
    """)
    conn.commit()
    conn.close()
    print("✅ SQLite DB ready.")

# === DATABASE ACTIONS ===
def get_user(user_id):
    with sqlite3.connect(DB_NAME) as conn:
        c = conn.cursor()
        c.execute("SELECT * FROM users WHERE id=?", (user_id,))
        return c.fetchone()

def add_user(user_id, referrer=None):
    with sqlite3.connect(DB_NAME) as conn:
        c = conn.cursor()
        c.execute("INSERT OR IGNORE INTO users (id, balance, clicks, referrer) VALUES (?, 0, 0, ?)", (user_id, referrer))
        if referrer and referrer != user_id:
            c.execute("UPDATE users SET balance = balance + ? WHERE id=?", (REFERRAL_REWARD, referrer))
        conn.commit()

def update_click(user_id):
    with sqlite3.connect(DB_NAME) as conn:
        c = conn.cursor()
        c.execute("UPDATE users SET balance = balance + ?, clicks = clicks + 1 WHERE id=?", (CLICK_REWARD, user_id))
        c.execute("INSERT INTO click_logs (user_id) VALUES (?)", (user_id,))
        conn.commit()

def request_withdrawal(user_id, method):
    with sqlite3.connect(DB_NAME) as conn:
        c = conn.cursor()
        c.execute("SELECT balance FROM users WHERE id=?", (user_id,))
        bal = c.fetchone()
        if bal and bal[0] >= MIN_WITHDRAW:
            c.execute("INSERT INTO withdrawals (user_id, method, amount) VALUES (?, ?, ?)", (user_id, method, bal[0]))
            c.execute("UPDATE users SET balance = 0 WHERE id=?", (user_id,))
            conn.commit()
            return True
    return False

def list_withdrawals():
    with sqlite3.connect(DB_NAME) as conn:
        c = conn.cursor()
        c.execute("SELECT id, user_id, method, amount, created_at FROM withdrawals WHERE status='pending'")
        return c.fetchall()

def approve_withdrawal(rowid):
    with sqlite3.connect(DB_NAME) as conn:
        c = conn.cursor()
        c.execute("UPDATE withdrawals SET status='paid' WHERE id=?", (rowid,))
        conn.commit()

def reject_withdrawal(rowid):
    with sqlite3.connect(DB_NAME) as conn:
        c = conn.cursor()
        c.execute("SELECT user_id, amount FROM withdrawals WHERE id=?", (rowid,))
        result = c.fetchone()
        if result:
            user_id, amount = result
            c.execute("UPDATE withdrawals SET status='rejected' WHERE id=?", (rowid,))
            c.execute("UPDATE users SET balance = balance + ? WHERE id=?", (amount, user_id))
            conn.commit()

def get_stats():
    with sqlite3.connect(DB_NAME) as conn:
        c = conn.cursor()
        c.execute("SELECT COUNT(*) FROM users")
        total_users = c.fetchone()[0]
        
        c.execute("SELECT SUM(balance) FROM users")
        total_balance = c.fetchone()[0] or 0
        
        c.execute("SELECT SUM(clicks) FROM users")
        total_clicks = c.fetchone()[0] or 0
        
        c.execute("SELECT COUNT(*) FROM withdrawals WHERE status='pending'")
        pending_withdrawals = c.fetchone()[0]
        
        c.execute("SELECT SUM(amount) FROM withdrawals WHERE status='paid'")
        paid_amount = c.fetchone()[0] or 0
        
        return {
            'total_users': total_users,
            'total_balance': total_balance,
            'total_clicks': total_clicks,
            'pending_withdrawals': pending_withdrawals,
            'paid_amount': paid_amount
        }

def get_referral_stats():
    with sqlite3.connect(DB_NAME) as conn:
        c = conn.cursor()
        
        # Total referrals
        c.execute("SELECT COUNT(*) FROM users WHERE referrer IS NOT NULL")
        total_referrals = c.fetchone()[0]
        
        # Total referral rewards paid
        total_referral_rewards = total_referrals * REFERRAL_REWARD
        
        # Top referrer count
        c.execute("SELECT referrer, COUNT(*) FROM users WHERE referrer IS NOT NULL GROUP BY referrer ORDER BY COUNT(*) DESC LIMIT 1")
        top_referrer = c.fetchone()
        top_referrer_count = top_referrer[1] if top_referrer else 0
        
        # Referral rate
        c.execute("SELECT COUNT(*) FROM users")
        total_users = c.fetchone()[0]
        referral_rate = (total_referrals / total_users * 100) if total_users > 0 else 0
        
        return {
            'total_referrals': total_referrals,
            'total_referral_rewards': total_referral_rewards,
            'top_referrer_count': top_referrer_count,
            'referral_rate': referral_rate
        }

def get_top_referrers():
    with sqlite3.connect(DB_NAME) as conn:
        c = conn.cursor()
        c.execute("""
            SELECT referrer, COUNT(*) as referral_count, COUNT(*) * ? as earnings
            FROM users 
            WHERE referrer IS NOT NULL 
            GROUP BY referrer 
            ORDER BY referral_count DESC 
            LIMIT 10
        """, (REFERRAL_REWARD,))
        return c.fetchall()

def get_recent_referrals():
    with sqlite3.connect(DB_NAME) as conn:
        c = conn.cursor()
        c.execute("""
            SELECT id, referrer, created_at
            FROM users 
            WHERE referrer IS NOT NULL 
            ORDER BY created_at DESC 
            LIMIT 20
        """)
        return c.fetchall()

def get_all_users_with_referral_data():
    with sqlite3.connect(DB_NAME) as conn:
        c = conn.cursor()
        c.execute("""
            SELECT u.id, u.balance, u.clicks, u.referrer, u.created_at,
                   COALESCE(ref_count.count, 0) as referrals_made,
                   COALESCE(ref_count.count, 0) * ? as referral_earnings
            FROM users u
            LEFT JOIN (
                SELECT referrer, COUNT(*) as count
                FROM users 
                WHERE referrer IS NOT NULL 
                GROUP BY referrer
            ) ref_count ON u.id = ref_count.referrer
            ORDER BY u.created_at DESC
        """, (REFERRAL_REWARD,))
        return c.fetchall()

def get_click_stats():
    with sqlite3.connect(DB_NAME) as conn:
        c = conn.cursor()
        
        # Total clicks
        c.execute("SELECT SUM(clicks) FROM users")
        total_clicks = c.fetchone()[0] or 0
        
        # Today's clicks
        c.execute("SELECT COUNT(*) FROM click_logs WHERE DATE(clicked_at) = DATE('now')")
        today_clicks = c.fetchone()[0] or 0
        
        # Average clicks per user
        c.execute("SELECT AVG(clicks) FROM users WHERE clicks > 0")
        avg_clicks_per_user = c.fetchone()[0] or 0
        
        # Top clicker's clicks
        c.execute("SELECT MAX(clicks) FROM users")
        top_clicker_clicks = c.fetchone()[0] or 0
        
        return {
            'total_clicks': total_clicks,
            'today_clicks': today_clicks,
            'avg_clicks_per_user': avg_clicks_per_user,
            'top_clicker_clicks': top_clicker_clicks
        }

def get_top_clickers():
    with sqlite3.connect(DB_NAME) as conn:
        c = conn.cursor()
        c.execute("""
            SELECT id, clicks, clicks * ? as click_earnings
            FROM users 
            WHERE clicks > 0
            ORDER BY clicks DESC 
            LIMIT 10
        """, (CLICK_REWARD,))
        return c.fetchall()

def get_recent_clicks():
    with sqlite3.connect(DB_NAME) as conn:
        c = conn.cursor()
        c.execute("""
            SELECT user_id, clicked_at
            FROM click_logs 
            ORDER BY clicked_at DESC 
            LIMIT 20
        """)
        return c.fetchall()

def get_all_users_click_data():
    with sqlite3.connect(DB_NAME) as conn:
        c = conn.cursor()
        c.execute("""
            SELECT u.id, u.clicks, u.balance, u.clicks * ? as click_earnings,
                   cl.last_click, u.clicks as click_rate
            FROM users u
            LEFT JOIN (
                SELECT user_id, MAX(clicked_at) as last_click
                FROM click_logs 
                GROUP BY user_id
            ) cl ON u.id = cl.user_id
            ORDER BY u.clicks DESC
        """, (CLICK_REWARD,))
        return c.fetchall()

def reset_user_balance(user_id):
    """Reset a user's balance to 0"""
    with sqlite3.connect(DB_NAME) as conn:
        c = conn.cursor()
        c.execute("UPDATE users SET balance = 0 WHERE id=?", (user_id,))
        conn.commit()
        return c.rowcount > 0

def reset_all_balances():
    """Reset all users' balances to 0"""
    with sqlite3.connect(DB_NAME) as conn:
        c = conn.cursor()
        c.execute("UPDATE users SET balance = 0")
        conn.commit()
        return c.rowcount

def log_link_click(user_id, ip_address, user_agent, referrer=None):
    """Log when a user clicks the AdMaven link"""
    with sqlite3.connect(DB_NAME) as conn:
        c = conn.cursor()
        c.execute("INSERT INTO link_clicks (user_id, ip_address, user_agent, referrer) VALUES (?, ?, ?, ?)", 
                 (user_id, ip_address, user_agent, referrer))
        conn.commit()

def get_link_click_stats():
    """Get statistics about link clicks"""
    with sqlite3.connect(DB_NAME) as conn:
        c = conn.cursor()
        
        # Total link clicks
        c.execute("SELECT COUNT(*) FROM link_clicks")
        total_link_clicks = c.fetchone()[0] or 0
        
        # Today's link clicks
        c.execute("SELECT COUNT(*) FROM link_clicks WHERE DATE(clicked_at) = DATE('now')")
        today_link_clicks = c.fetchone()[0] or 0
        
        # Unique users who clicked links
        c.execute("SELECT COUNT(DISTINCT user_id) FROM link_clicks")
        unique_clickers = c.fetchone()[0] or 0
        
        # Average link clicks per user
        avg_link_clicks = total_link_clicks / unique_clickers if unique_clickers > 0 else 0
        
        return {
            'total_link_clicks': total_link_clicks,
            'today_link_clicks': today_link_clicks,
            'unique_clickers': unique_clickers,
            'avg_link_clicks': avg_link_clicks
        }

def get_recent_link_clicks():
    """Get recent link clicks with user info"""
    with sqlite3.connect(DB_NAME) as conn:
        c = conn.cursor()
        c.execute("""
            SELECT user_id, ip_address, clicked_at, user_agent
            FROM link_clicks 
            ORDER BY clicked_at DESC 
            LIMIT 50
        """)
        return c.fetchall()

def get_top_link_clickers():
    """Get users who click links most frequently"""
    with sqlite3.connect(DB_NAME) as conn:
        c = conn.cursor()
        c.execute("""
            SELECT user_id, COUNT(*) as link_clicks, MAX(clicked_at) as last_click
            FROM link_clicks 
            GROUP BY user_id 
            ORDER BY link_clicks DESC 
            LIMIT 20
        """)
        return c.fetchall()

# === TELEGRAM BOT HANDLERS ===

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    args = context.args
    ref = int(args[0]) if args and args[0].isdigit() else None
    add_user(user_id, referrer=ref)

    keyboard = [
        [InlineKeyboardButton("🖱 Click & Earn", callback_data="click")],
        [InlineKeyboardButton("💰 Balance", callback_data="balance")],
        [InlineKeyboardButton("👥 Referral Link", callback_data="referral")],
        [InlineKeyboardButton("💸 Withdraw", callback_data="withdraw")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text("👋 Welcome! Earn by clicking the button below.", reply_markup=reply_markup)

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = query.from_user.id
    await query.answer()

    if query.data == "click":
        update_click(user_id)
        # Get bot domain from context or use a placeholder
        bot_domain = "click-earn-bot.replit.dev"  # Replace with your actual repl URL
        tracking_link = f"https://{bot_domain}/track_link/{user_id}"
        keyboard = [[InlineKeyboardButton("🔙 Back to Menu", callback_data="menu")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text(f"✅ Click registered! +${CLICK_REWARD}\n🔗 Visit: {tracking_link}", reply_markup=reply_markup)
    elif query.data == "balance":
        user = get_user(user_id)
        keyboard = [[InlineKeyboardButton("🔙 Back to Menu", callback_data="menu")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text(f"💰 Your balance: ${user[1]:.4f}\n🖱 Total clicks: {user[2]}", reply_markup=reply_markup)
    elif query.data == "referral":
        # Get bot username
        bot_info = await context.bot.get_me()
        bot_username = bot_info.username
        keyboard = [[InlineKeyboardButton("🔙 Back to Menu", callback_data="menu")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text(f"🔗 Your referral link:\nhttps://t.me/{bot_username}?start={user_id}", reply_markup=reply_markup)
    elif query.data == "withdraw":
        user = get_user(user_id)
        if user[1] >= MIN_WITHDRAW:
            await query.edit_message_text("💳 Send your wallet or method (e.g. USDT address / Opay number):")
            context.user_data["awaiting_withdraw"] = True
        else:
            keyboard = [[InlineKeyboardButton("🔙 Back to Menu", callback_data="menu")]]
            reply_markup = InlineKeyboardMarkup(keyboard)
            await query.edit_message_text(f"❌ Minimum withdrawal is ${MIN_WITHDRAW:.2f}.", reply_markup=reply_markup)
    elif query.data == "menu":
        keyboard = [
            [InlineKeyboardButton("🖱 Click & Earn", callback_data="click")],
            [InlineKeyboardButton("💰 Balance", callback_data="balance")],
            [InlineKeyboardButton("👥 Referral Link", callback_data="referral")],
            [InlineKeyboardButton("💸 Withdraw", callback_data="withdraw")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text("👋 Welcome! Earn by clicking the button below.", reply_markup=reply_markup)

async def message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if context.user_data.get("awaiting_withdraw"):
        method = update.message.text
        if request_withdrawal(user_id, method):
            await update.message.reply_text(f"✅ Withdrawal request submitted.\nYou'll be paid via: {method}")
            context.user_data["awaiting_withdraw"] = False
        else:
            await update.message.reply_text("❌ Error: Insufficient balance or database issue.")

async def admin_check(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("❌ Access denied.")
        return
    rows = list_withdrawals()
    if not rows:
        await update.message.reply_text("✅ No pending withdrawals.")
        return
    for row in rows:
        await update.message.reply_text(
            f"🆔 Request ID: {row[0]}\n👤 User: {row[1]}\n💸 ${row[3]:.2f}\nMethod: {row[2]}\n📅 {row[4]}",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("✅ Approve", callback_data=f"approve_{row[0]}"),
                 InlineKeyboardButton("❌ Reject", callback_data=f"reject_{row[0]}")]
            ])
        )

async def approve_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query.from_user.id != ADMIN_ID:
        return
    rowid = int(query.data.split("_")[1])
    approve_withdrawal(rowid)
    await query.answer("✅ Approved")
    await query.edit_message_text("✅ Marked as paid.")

async def reject_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query.from_user.id != ADMIN_ID:
        return
    rowid = int(query.data.split("_")[1])
    reject_withdrawal(rowid)
    await query.answer("❌ Rejected")
    await query.edit_message_text("❌ Request rejected. Balance restored to user.")

async def leaderboard(update: Update, context: ContextTypes.DEFAULT_TYPE):
    with sqlite3.connect(DB_NAME) as conn:
        c = conn.cursor()
        c.execute("SELECT id, balance FROM users ORDER BY balance DESC LIMIT 5")
        top = c.fetchall()

    msg = "🏆 Top Earners:\n"
    for i, (uid, bal) in enumerate(top, 1):
        msg += f"{i}. User {uid}: ${bal:.2f}\n"
    await update.message.reply_text(msg)

async def history(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    with sqlite3.connect(DB_NAME) as conn:
        c = conn.cursor()
        c.execute("SELECT method, amount, created_at FROM withdrawals WHERE user_id=? AND status='paid'", (user_id,))
        rows = c.fetchall()

    if not rows:
        await update.message.reply_text("🕐 No payout history yet.")
    else:
        msg = "📜 Your Withdrawals:\n"
        for method, amount, date in rows:
            msg += f"• ${amount:.2f} to {method} ({date[:10]})\n"
        await update.message.reply_text(msg)

async def reset_balance_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("❌ Access denied.")
        return
    
    args = context.args
    if not args:
        await update.message.reply_text("📋 Usage:\n/resetbalance <user_id> - Reset specific user\n/resetbalance all - Reset all users")
        return
    
    if args[0].lower() == "all":
        count = reset_all_balances()
        await update.message.reply_text(f"✅ Reset balances for {count} users.")
    elif args[0].isdigit():
        user_id = int(args[0])
        if reset_user_balance(user_id):
            await update.message.reply_text(f"✅ Reset balance for user {user_id}.")
        else:
            await update.message.reply_text(f"❌ User {user_id} not found.")
    else:
        await update.message.reply_text("❌ Invalid user ID. Use a number or 'all'.")

# === WEB ADMIN DASHBOARD ===

app = Flask(__name__, template_folder='templates')

@app.route('/')
def dashboard():
    stats = get_stats()
    return render_template('dashboard.html', stats=stats)

@app.route('/users')
def users():
    with sqlite3.connect(DB_NAME) as conn:
        c = conn.cursor()
        c.execute("SELECT id, balance, clicks, referrer, created_at FROM users ORDER BY created_at DESC")
        users_data = c.fetchall()
    return render_template('users.html', users=users_data)

@app.route('/withdrawals')
def withdrawals():
    with sqlite3.connect(DB_NAME) as conn:
        c = conn.cursor()
        c.execute("SELECT id, user_id, method, amount, status, created_at FROM withdrawals ORDER BY created_at DESC")
        withdrawals_data = c.fetchall()
    return render_template('withdrawals.html', withdrawals=withdrawals_data)

@app.route('/referrals')
def referrals():
    referral_stats = get_referral_stats()
    top_referrers = get_top_referrers()
    recent_referrals = get_recent_referrals()
    all_users = get_all_users_with_referral_data()
    
    return render_template('referrals.html', 
                         referral_stats=referral_stats,
                         top_referrers=top_referrers,
                         recent_referrals=recent_referrals,
                         all_users=all_users)

@app.route('/clicks')
def clicks():
    click_stats = get_click_stats()
    top_clickers = get_top_clickers()
    recent_clicks = get_recent_clicks()
    all_users_clicks = get_all_users_click_data()
    
    return render_template('clicks.html',
                         click_stats=click_stats,
                         top_clickers=top_clickers,
                         recent_clicks=recent_clicks,
                         all_users_clicks=all_users_clicks)

@app.route('/api/approve/<int:withdrawal_id>')
def api_approve(withdrawal_id):
    approve_withdrawal(withdrawal_id)
    return jsonify({'status': 'success'})

@app.route('/api/reject/<int:withdrawal_id>')
def api_reject(withdrawal_id):
    reject_withdrawal(withdrawal_id)
    return jsonify({'status': 'success'})

@app.route('/api/stats')
def api_stats():
    return jsonify(get_stats())

@app.route('/api/reset_balance/<int:user_id>')
def api_reset_balance(user_id):
    if reset_user_balance(user_id):
        return jsonify({'status': 'success', 'message': f'Balance reset for user {user_id}'})
    else:
        return jsonify({'status': 'error', 'message': 'User not found'}), 404

@app.route('/api/reset_all_balances')
def api_reset_all_balances():
    count = reset_all_balances()
    return jsonify({'status': 'success', 'message': f'Reset balances for {count} users'})

@app.route('/track_link/<int:user_id>')
def track_link(user_id):
    """Track when user clicks the AdMaven link"""
    ip_address = request.environ.get('HTTP_X_FORWARDED_FOR', request.environ.get('REMOTE_ADDR', 'Unknown'))
    user_agent = request.headers.get('User-Agent', 'Unknown')
    referrer = request.headers.get('Referer', 'Unknown')
    
    # Log the click
    log_link_click(user_id, ip_address, user_agent, referrer)
    
    # Redirect to the actual AdMaven link
    return redirect(ADMAVEN_LINK)

@app.route('/link_monitoring')
def link_monitoring():
    link_stats = get_link_click_stats()
    recent_clicks = get_recent_link_clicks()
    top_clickers = get_top_link_clickers()
    
    return render_template('link_monitoring.html',
                         link_stats=link_stats,
                         recent_clicks=recent_clicks,
                         top_clickers=top_clickers)

@app.route('/keep_alive')
def keep_alive():
    return "Bot is alive!", 200

@app.route('/health')
def health_check():
    return jsonify({
        'status': 'healthy',
        'timestamp': datetime.now().isoformat(),
        'bot_running': True
    })

@app.route('/ping')
def ping():
    return "pong", 200

def keep_alive_worker():
    """Background worker to keep the app alive"""
    import requests
    import time
    
    time.sleep(10)  # Wait for server to start
    
    while True:
        try:
            # Make a request to ourselves every 4 minutes
            requests.get('http://127.0.0.1:5000/ping', timeout=10)
            print("🔄 Keep-alive ping sent")
        except Exception as e:
            print(f"⚠️ Keep-alive ping failed: {e}")
        
        time.sleep(240)  # 4 minutes

def run_web():
    import socket
    # Find an available port
    port = 5000
    while True:
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.bind(('0.0.0.0', port))
                break
        except OSError:
            port += 1
            if port > 5010:  # Limit search to avoid infinite loop
                port = 5000
                break
    
    try:
        app.run(host="0.0.0.0", port=port, debug=False, use_reloader=False)
    except Exception as e:
        print(f"⚠️ Web server error: {e}")

# === MAIN FUNCTION ===

def signal_handler(sig, frame):
    print('\n💤 Shutting down gracefully...')
    sys.exit(0)

def main():
    try:
        print("🚀 Starting Click & Earn Bot...")
        init_db()

        # Set up signal handlers for graceful shutdown
        signal.signal(signal.SIGINT, signal_handler)
        signal.signal(signal.SIGTERM, signal_handler)

        # Run web dashboard in background
        web_thread = threading.Thread(target=run_web, daemon=True)
        web_thread.start()
        time.sleep(2)  # Give web server time to start
        
        # Start keep-alive worker
        keep_alive_thread = threading.Thread(target=keep_alive_worker, daemon=True)
        keep_alive_thread.start()

        # Build the application
        app_bot = Application.builder().token(BOT_TOKEN).build()

        # Add handlers
        app_bot.add_handler(CommandHandler("start", start))
        app_bot.add_handler(CommandHandler("leaderboard", leaderboard))
        app_bot.add_handler(CommandHandler("history", history))
        app_bot.add_handler(CommandHandler("admin", admin_check))
        app_bot.add_handler(CommandHandler("resetbalance", reset_balance_cmd))

        app_bot.add_handler(CallbackQueryHandler(button_handler))
        app_bot.add_handler(CallbackQueryHandler(approve_handler, pattern="^approve_"))
        app_bot.add_handler(CallbackQueryHandler(reject_handler, pattern="^reject_"))
        app_bot.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, message_handler))

        print("✅ Bot is running...")
        print("🌐 Admin Dashboard: Check your webview for the dashboard")
        print("💚 Keep alive endpoint: /keep_alive")
        
        # Start polling (this is blocking)
        app_bot.run_polling(drop_pending_updates=True)
        
    except KeyboardInterrupt:
        print("\n💤 Bot stopped by user")
    except Exception as e:
        print(f"❌ Bot error: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    main()
