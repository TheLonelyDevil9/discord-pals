"""
Discord Pals - Statistics Tracking
Tracks message counts, response times, and user activity.
"""

import json
import os
import time
from datetime import datetime, timedelta
from typing import Dict, List, Optional
from config import DATA_DIR

STATS_FILE = os.path.join(DATA_DIR, "stats.json")

# Default stats structure
DEFAULT_STATS = {
    "created_at": None,
    "total_messages_received": 0,
    "total_responses_sent": 0,
    "total_response_time_ms": 0,
    "daily_stats": {},  # {"2026-01-01": {"messages": 10, "responses": 8}}
    "user_stats": {},   # {"user_id": {"messages": 5, "name": "Username"}}
    "channel_stats": {} # {"channel_id": {"messages": 10, "name": "#channel"}}
}


class StatsManager:
    """Manages bot statistics."""
    
    def __init__(self):
        self.stats = self._load_stats()
    
    def _load_stats(self) -> dict:
        """Load stats from file."""
        if os.path.exists(STATS_FILE):
            try:
                with open(STATS_FILE, 'r', encoding='utf-8') as f:
                    stats = json.load(f)
                    # Merge with defaults for any missing keys
                    for key, value in DEFAULT_STATS.items():
                        if key not in stats:
                            stats[key] = value
                    return stats
            except (json.JSONDecodeError, IOError):
                pass
        
        stats = DEFAULT_STATS.copy()
        stats["created_at"] = datetime.now().isoformat()
        return stats
    
    def _save_stats(self):
        """Save stats to file."""
        os.makedirs(DATA_DIR, exist_ok=True)
        with open(STATS_FILE, 'w', encoding='utf-8') as f:
            json.dump(self.stats, f, indent=2)
    
    def _today(self) -> str:
        """Get today's date string."""
        return datetime.now().strftime("%Y-%m-%d")
    
    def record_message(self, user_id: int, user_name: str, channel_id: int, channel_name: str):
        """Record an incoming message."""
        self.stats["total_messages_received"] += 1
        
        # Daily stats
        today = self._today()
        if today not in self.stats["daily_stats"]:
            self.stats["daily_stats"][today] = {"messages": 0, "responses": 0, "response_time_ms": 0}
        self.stats["daily_stats"][today]["messages"] += 1
        
        # User stats
        user_key = str(user_id)
        if user_key not in self.stats["user_stats"]:
            self.stats["user_stats"][user_key] = {"messages": 0, "name": user_name}
        self.stats["user_stats"][user_key]["messages"] += 1
        self.stats["user_stats"][user_key]["name"] = user_name  # Update name
        
        # Channel stats
        channel_key = str(channel_id)
        if channel_key not in self.stats["channel_stats"]:
            self.stats["channel_stats"][channel_key] = {"messages": 0, "name": channel_name}
        self.stats["channel_stats"][channel_key]["messages"] += 1
        self.stats["channel_stats"][channel_key]["name"] = channel_name
        
        self._save_stats()
    
    def record_response(self, response_time_ms: int):
        """Record a bot response with timing."""
        self.stats["total_responses_sent"] += 1
        self.stats["total_response_time_ms"] += response_time_ms
        
        today = self._today()
        if today not in self.stats["daily_stats"]:
            self.stats["daily_stats"][today] = {"messages": 0, "responses": 0, "response_time_ms": 0}
        self.stats["daily_stats"][today]["responses"] += 1
        self.stats["daily_stats"][today]["response_time_ms"] += response_time_ms
        
        self._save_stats()
    
    def get_summary(self) -> dict:
        """Get stats summary for dashboard."""
        avg_response_time = 0
        if self.stats["total_responses_sent"] > 0:
            avg_response_time = self.stats["total_response_time_ms"] // self.stats["total_responses_sent"]
        
        # Get top users
        top_users = sorted(
            self.stats["user_stats"].items(),
            key=lambda x: x[1]["messages"],
            reverse=True
        )[:10]
        
        # Get top channels
        top_channels = sorted(
            self.stats["channel_stats"].items(),
            key=lambda x: x[1]["messages"],
            reverse=True
        )[:10]
        
        # Get last 7 days
        recent_days = []
        for i in range(6, -1, -1):
            date = (datetime.now() - timedelta(days=i)).strftime("%Y-%m-%d")
            if date in self.stats["daily_stats"]:
                recent_days.append({
                    "date": date,
                    **self.stats["daily_stats"][date]
                })
            else:
                recent_days.append({"date": date, "messages": 0, "responses": 0})
        
        return {
            "created_at": self.stats.get("created_at"),
            "total_messages": self.stats["total_messages_received"],
            "total_responses": self.stats["total_responses_sent"],
            "avg_response_time_ms": avg_response_time,
            "top_users": top_users,
            "top_channels": top_channels,
            "recent_days": recent_days
        }
    
    def get_user_name(self, user_id: int) -> Optional[str]:
        """Get cached user name by ID."""
        user_key = str(user_id)
        if user_key in self.stats["user_stats"]:
            return self.stats["user_stats"][user_key].get("name")
        return None
    
    def get_all_user_names(self) -> Dict[str, str]:
        """Get all cached user ID -> name mappings."""
        return {uid: data.get("name", f"User {uid}") for uid, data in self.stats["user_stats"].items()}


# Global instance
stats_manager = StatsManager()
