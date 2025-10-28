# Enhanced SlackNotifier with separate channels, daily summaries, and error categorization
import json
import socket
import requests
import threading
import time
import subprocess
from datetime import datetime, timezone
from typing import Dict, Optional, Any
from collections import defaultdict
import bittensor as bt
from utils.weight_failure_classifier import WeightFailureClassifier


class SlackNotifier:
    """Handles all Slack notifications for miners and validators with enhanced features"""

    def __init__(self, hotkey, webhook_url: Optional[str] = None, error_webhook_url: Optional[str] = None,
                 is_miner: bool = False):
        self.webhook_url = webhook_url
        self.hotkey = hotkey
        self.error_webhook_url = error_webhook_url or webhook_url  # Fallback to main if not provided
        self.enabled = bool(webhook_url)
        self.is_miner = is_miner
        self.node_type = "Miner" if is_miner else "Validator"
        self.vm_ip = self._get_vm_ip()
        self.vm_hostname = self._get_vm_hostname()
        self.git_branch = self._get_git_branch()

        # Daily summary tracking
        self.startup_time = datetime.now(timezone.utc)
        self.daily_summary_lock = threading.Lock()
        self.last_summary_date = None

        # Persistent metrics (survive restarts)
        self.metrics_file = f"{self.node_type.lower()}_lifetime_metrics.json"
        self.lifetime_metrics = self._load_lifetime_metrics()

        # Daily metrics (reset each day)
        if self.is_miner:
            # Stake sweeper metrics
            self.daily_metrics = {
                "stake_sweeps_count": 0,
                "stake_sweeps_failed": 0,
                "stake_transfers_count": 0,
                "stake_transfers_failed": 0,
                "total_stake_swept": 0.0,  # in TAO
                "total_stake_transferred": 0.0,  # in TAO
            }
        else:
            # Validator-specific metrics
            self.daily_metrics = {
                "weights_set_count": 0,
                "weights_set_failed": 0,
                "weights_set_times": [],  # Timestamps of when weights were set
                "no_permit_events": 0,
                "registration_failures": 0,
                "burn_uid_changes": []  # Track if burn UID changes
            }

        # Start daily summary thread
        self._start_daily_summary_thread()

    def _get_vm_ip(self) -> str:
        """Get the VM's IP address"""
        try:
            response = requests.get('https://api.ipify.org', timeout=5)
            return response.text
        except Exception as e:
            try:
                bt.logging.error(f"Got exception: {e}")
                hostname = socket.gethostname()
                return socket.gethostbyname(hostname)
            except Exception as e2:
                bt.logging.error(f"Got exception: {e2}")
                return "Unknown IP"

    def _get_vm_hostname(self) -> str:
        """Get the VM's hostname"""
        try:
            return socket.gethostname()
        except Exception as e:
            bt.logging.error(f"Got exception: {e}")
            return "Unknown Hostname"

    def _get_git_branch(self) -> str:
        """Get the current git branch"""
        try:
            result = subprocess.run(
                ['git', 'rev-parse', '--abbrev-ref', 'HEAD'],
                capture_output=True,
                text=True,
                check=True
            )
            branch = result.stdout.strip()
            if branch:
                return branch
            return "Unknown Branch"
        except Exception as e:
            bt.logging.error(f"Failed to get git branch: {e}")
            return "Unknown Branch"

    def _load_lifetime_metrics(self) -> Dict[str, Any]:
        """Load persistent metrics from file
        try:
            if os.path.exists(self.metrics_file):
                with open(self.metrics_file, 'r') as f:
                    return json.load(f)
        except Exception as e:
            bt.logging.warning(f"Failed to load lifetime metrics: {e}")
        """
        # Default metrics
        if self.is_miner:
            return {
                "total_lifetime_stake_swept": 0.0,
                "total_lifetime_stake_transferred": 0.0,
                "total_uptime_seconds": 0,
                "last_shutdown_time": None
            }
        else:
            return {
                "total_lifetime_weights_set": 0,
                "total_uptime_seconds": 0,
                "last_shutdown_time": None
            }

    def _save_lifetime_metrics(self):
        """Save persistent metrics to file"""
        try:
            # Update uptime
            if self.lifetime_metrics.get("last_shutdown_time"):
                last_shutdown = datetime.fromisoformat(self.lifetime_metrics["last_shutdown_time"])
                downtime = (self.startup_time - last_shutdown).total_seconds()
                # Only add if downtime was reasonable (less than 7 days)
                if 0 < downtime < 7 * 24 * 3600:
                    pass  # Don't add downtime to uptime

            current_session_uptime = (datetime.now(timezone.utc) - self.startup_time).total_seconds()
            self.lifetime_metrics["total_uptime_seconds"] += current_session_uptime
            self.lifetime_metrics["last_shutdown_time"] = datetime.now(timezone.utc).isoformat()

            with open(self.metrics_file, 'w') as f:
                json.dump(self.lifetime_metrics, f)
        except Exception as e:
            bt.logging.error(f"Failed to save lifetime metrics: {e}")

    def _start_daily_summary_thread(self):
        """Start the daily summary thread"""
        if not self.enabled:
            return

        def daily_summary_loop():
            while True:
                try:
                    now = datetime.now(timezone.utc)
                    # Calculate seconds until next midnight UTC
                    next_midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
                    if next_midnight <= now:
                        next_midnight = next_midnight.replace(day=next_midnight.day + 1)

                    sleep_seconds = (next_midnight - now).total_seconds()
                    time.sleep(sleep_seconds)

                    # Send daily summary (only makes sense for miners at this moment)
                    if self.is_miner:
                        self._send_daily_summary_miner()
                    else:
                        self._send_daily_summary_validator()


                except Exception as e:
                    bt.logging.error(f"Error in daily summary thread: {e}")
                    time.sleep(3600)  # Sleep 1 hour on error

        summary_thread = threading.Thread(target=daily_summary_loop, daemon=True)
        summary_thread.start()

    def _get_uptime_str(self) -> str:
        """Get formatted uptime string"""
        current_uptime = (datetime.now(timezone.utc) - self.startup_time).total_seconds()
        total_uptime = self.lifetime_metrics["total_uptime_seconds"] + current_uptime

        if total_uptime >= 86400:
            return f"{total_uptime / 86400:.1f} days"
        else:
            return f"{total_uptime / 3600:.1f} hours"

    def _send_daily_summary_validator(self):
        """Send daily summary report for validators"""
        with self.daily_summary_lock:
            try:
                # Calculate uptime
                uptime_str = self._get_uptime_str()

                # Calculate success rate for weight setting
                total_attempts = self.daily_metrics["weights_set_count"] + self.daily_metrics["weights_set_failed"]
                if total_attempts > 0:
                    success_rate = (self.daily_metrics["weights_set_count"] / total_attempts) * 100
                else:
                    success_rate = 0.0

                # Calculate average time between weight sets
                weight_times = self.daily_metrics["weights_set_times"]
                if len(weight_times) > 1:
                    time_diffs = []
                    for i in range(1, len(weight_times)):
                        diff = (weight_times[i] - weight_times[i-1]).total_seconds() / 60  # in minutes
                        time_diffs.append(diff)
                    avg_interval = sum(time_diffs) / len(time_diffs)
                    avg_interval_str = f"{avg_interval:.1f} minutes"
                else:
                    avg_interval_str = "N/A"

                fields = [
                    {
                        "title": "📊 Daily Summary Report",
                        "value": f"Automated daily report for {datetime.now(timezone.utc).strftime('%Y-%m-%d')}",
                        "short": False
                    },
                    {
                        "title": "🕒 Validator Hotkey",
                        "value": f"...{self.hotkey[-8:]}",
                        "short": True
                    },
                    {
                        "title": "Script Uptime",
                        "value": uptime_str,
                        "short": True
                    },
                    {
                        "title": "📈 Lifetime Weights Set",
                        "value": str(self.lifetime_metrics["total_lifetime_weights_set"]),
                        "short": True
                    },
                    {
                        "title": "📅 Today's Weights Set",
                        "value": str(self.daily_metrics["weights_set_count"]),
                        "short": True
                    },
                    {
                        "title": "✅ Weight Set Success Rate",
                        "value": f"{success_rate:.1f}%",
                        "short": True
                    },
                    {
                        "title": "⏱️ Avg Interval Between Weight Sets",
                        "value": avg_interval_str,
                        "short": True
                    },
                    {
                        "title": "🖥️ System Info",
                        "value": f"Host: {self.vm_hostname}\nIP: {self.vm_ip}\nBranch: {self.git_branch}",
                        "short": True
                    }
                ]

                # Add failure details if any
                if self.daily_metrics["weights_set_failed"] > 0:
                    fields.append({
                        "title": "❌ Failed Weight Sets",
                        "value": str(self.daily_metrics["weights_set_failed"]),
                        "short": True
                    })

                if self.daily_metrics["no_permit_events"] > 0:
                    fields.append({
                        "title": "⚠️ No Permit Events",
                        "value": str(self.daily_metrics["no_permit_events"]),
                        "short": True
                    })

                if self.daily_metrics["registration_failures"] > 0:
                    fields.append({
                        "title": "🚫 Registration Failures",
                        "value": str(self.daily_metrics["registration_failures"]),
                        "short": True
                    })

                if self.daily_metrics["burn_uid_changes"]:
                    changes_str = "\n".join([
                        f"Changed from {old} to {new} at {time.strftime('%H:%M:%S')}"
                        for old, new, time in self.daily_metrics["burn_uid_changes"]
                    ])
                    fields.append({
                        "title": "🔄 Burn UID Changes",
                        "value": changes_str,
                        "short": False
                    })

                payload = {
                    "attachments": [{
                        "color": "#4CAF50",  # Green for summary
                        "fields": fields,
                        "footer": f"Taoshi {self.node_type} Daily Summary",
                        "ts": int(time.time())
                    }]
                }

                # Send to main channel (not error channel)
                response = requests.post(self.webhook_url, json=payload, timeout=10)
                response.raise_for_status()

                # Reset daily metrics after successful send
                self.daily_metrics = {
                    "weights_set_count": 0,
                    "weights_set_failed": 0,
                    "weights_set_times": [],
                    "no_permit_events": 0,
                    "registration_failures": 0,
                    "burn_uid_changes": []
                }

            except Exception as e:
                bt.logging.error(f"Failed to send daily summary: {e}")

    def _send_daily_summary_miner(self):
        """Send daily summary report for stake sweeper miners"""
        with self.daily_summary_lock:
            try:
                # Calculate uptime
                uptime_str = self._get_uptime_str()

                # Calculate success rates
                sweep_success_rate = 0.0
                total_sweeps = self.daily_metrics["stake_sweeps_count"] + self.daily_metrics["stake_sweeps_failed"]
                if total_sweeps > 0:
                    sweep_success_rate = (self.daily_metrics["stake_sweeps_count"] / total_sweeps) * 100

                transfer_success_rate = 0.0
                total_transfers = self.daily_metrics["stake_transfers_count"] + self.daily_metrics["stake_transfers_failed"]
                if total_transfers > 0:
                    transfer_success_rate = (self.daily_metrics["stake_transfers_count"] / total_transfers) * 100

                fields = [
                    {
                        "title": "📊 Daily Summary Report",
                        "value": f"Automated daily report for {datetime.now(timezone.utc).strftime('%Y-%m-%d')}",
                        "short": False
                    },
                    {
                        "title": f"🕒 {self.node_type} Hotkey",
                        "value": f"...{self.hotkey[-8:]}",
                        "short": True
                    },
                    {
                        "title": "Script Uptime",
                        "value": uptime_str,
                        "short": True
                    },
                    {
                        "title": "🔄 Stake Sweeps",
                        "value": f"Success: {self.daily_metrics['stake_sweeps_count']}, Failed: {self.daily_metrics['stake_sweeps_failed']}, Rate: {sweep_success_rate:.1f}%",
                        "short": False
                    },
                    {
                        "title": "🚚 Stake Transfers",
                        "value": f"Success: {self.daily_metrics['stake_transfers_count']}, Failed: {self.daily_metrics['stake_transfers_failed']}, Rate: {transfer_success_rate:.1f}%",
                        "short": False
                    },
                    {
                        "title": "💰 Today's Stake Swept",
                        "value": f"{self.daily_metrics['total_stake_swept']:.9f} α",
                        "short": True
                    },
                    {
                        "title": "💸 Today's Stake Transferred",
                        "value": f"{self.daily_metrics['total_stake_transferred']:.9f} α",
                        "short": True
                    },
                    {
                        "title": "📈 Lifetime Stake Swept",
                        "value": f"{self.lifetime_metrics['total_lifetime_stake_swept']:.9f} α",
                        "short": True
                    },
                    {
                        "title": "📈 Lifetime Stake Transferred",
                        "value": f"{self.lifetime_metrics['total_lifetime_stake_transferred']:.9f} α",
                        "short": True
                    },
                    {
                        "title": "🖥️ System Info",
                        "value": f"Host: {self.vm_hostname}\nIP: {self.vm_ip}\nBranch: {self.git_branch}",
                        "short": True
                    }
                ]

                payload = {
                    "attachments": [{
                        "color": "#4CAF50",  # Green for summary
                        "fields": fields,
                        "footer": f"Taoshi {self.node_type} Daily Summary",
                        "ts": int(time.time())
                    }]
                }

                # Send to main channel (not error channel)
                response = requests.post(self.webhook_url, json=payload, timeout=10)
                response.raise_for_status()

                # Update lifetime metrics
                self.lifetime_metrics["total_lifetime_stake_swept"] += self.daily_metrics["total_stake_swept"]
                self.lifetime_metrics["total_lifetime_stake_transferred"] += self.daily_metrics["total_stake_transferred"]

                # Reset daily metrics after successful send
                self.daily_metrics = {
                    "stake_sweeps_count": 0,
                    "stake_sweeps_failed": 0,
                    "stake_transfers_count": 0,
                    "stake_transfers_failed": 0,
                    "total_stake_swept": 0.0,
                    "total_stake_transferred": 0.0,
                }

            except Exception as e:
                bt.logging.error(f"Failed to send daily summary: {e}")

    def send_message(self, message: str, level: str = "info"):
        """Send a message to appropriate Slack channel based on level"""
        if not self.enabled:
            return

        try:
            # Determine which webhook to use
            if level in ["error", "warning"]:
                webhook_url = self.error_webhook_url
            else:
                webhook_url = self.webhook_url

            # Color coding for different message levels
            color_map = {
                "error": "#ff0000",
                "warning": "#ff9900",
                "success": "#00ff00",
                "info": "#0099ff"
            }

            payload = {
                "attachments": [{
                    "color": color_map.get(level, "#808080"),
                    "fields": [
                        {
                            "title": f"{self.node_type} Alert",
                            "value": message,
                            "short": False
                        },
                        {
                            "title": f"VM IP | {self.node_type} Hotkey",
                            "value": f"{self.vm_ip} | ...{self.hotkey[-8:]}",
                            "short": True
                        },
                        {
                            "title": "Script Uptime | Git Branch",
                            "value": f"{self._get_uptime_str()} | {self.git_branch}",
                            "short": True
                        }
                    ],
                    "footer": f"Taoshi {self.node_type} Notification",
                    "ts": int(time.time())
                }]
            }

            response = requests.post(webhook_url, json=payload, timeout=10)
            response.raise_for_status()

        except Exception as e:
            bt.logging.error(f"Failed to send Slack notification: {e}")

    def _classify_weight_failure(self, error_msg: str) -> str:
        """Classify weight setting failures using centralized classifier"""
        return WeightFailureClassifier.classify_failure(error_msg)

    def _should_alert_weight_failure(self, failure_type: str, consecutive_failures: int,
                                     time_since_success: float, time_since_last_alert: float) -> bool:
        """Determine if we should send a weight failure alert"""
        # Alert if we haven't had a successful weight setting in 2 hours (absolute timeout)
        if time_since_success > 7200:  # 2 hours
            return True

        # Rate limiting - but exempt critical errors and 1+ hour timeouts
        if failure_type != "critical" and time_since_success <= 3600:
            if time_since_last_alert < 600:  # 10 minutes
                return False

        # Always alert for known critical errors (no rate limiting)
        if failure_type == "critical":
            return True

        # Alert if we haven't had a successful weight setting in 1 hour
        if time_since_success > 3600:
            return True

        # Never alert for benign "too soon" errors (unless prolonged, caught above)
        if failure_type == "benign":
            return False

        # For unknown errors, alert after 2 consecutive failures
        if failure_type == "unknown" and consecutive_failures >= 2:
            return True

        return False

    def record_weight_set_success(self) -> bool:
        """Record a successful weight set for validators

        Returns:
            bool: True if a recovery alert should be sent
        """
        if not self.is_miner:
            with self.daily_summary_lock:
                # Check if we should send recovery alert
                should_send_recovery = (
                    hasattr(self, '_weight_consecutive_failures') and
                    self._weight_consecutive_failures > 0 and
                    hasattr(self, '_weight_had_critical_failure') and
                    self._weight_had_critical_failure
                )

                # Update metrics
                self.daily_metrics["weights_set_count"] += 1
                self.daily_metrics["weights_set_times"].append(datetime.now(timezone.utc))
                self.lifetime_metrics["total_lifetime_weights_set"] += 1

                # Reset failure tracking
                self._weight_consecutive_failures = 0
                self._weight_last_success_time = time.time()
                self._weight_had_critical_failure = False

                return should_send_recovery
        return False

    def record_weight_set_failure(self, error_msg: str) -> tuple[bool, str]:
        """Record a failed weight set for validators

        Args:
            error_msg: The error message from the weight setting attempt

        Returns:
            tuple: (should_alert, failure_type)
        """
        if not self.is_miner:
            with self.daily_summary_lock:
                # Initialize tracking variables if not present
                if not hasattr(self, '_weight_consecutive_failures'):
                    self._weight_consecutive_failures = 0
                if not hasattr(self, '_weight_last_success_time'):
                    self._weight_last_success_time = time.time()
                if not hasattr(self, '_weight_last_alert_time'):
                    self._weight_last_alert_time = 0
                if not hasattr(self, '_weight_had_critical_failure'):
                    self._weight_had_critical_failure = False

                # Classify the failure
                failure_type = self._classify_weight_failure(error_msg)

                # Track failure
                self._weight_consecutive_failures += 1
                if failure_type == "critical":
                    self._weight_had_critical_failure = True

                # Only increment daily metric for non-benign failures
                if failure_type != "benign":
                    self.daily_metrics["weights_set_failed"] += 1

                # Determine if we should alert
                time_since_success = time.time() - self._weight_last_success_time
                time_since_last_alert = time.time() - self._weight_last_alert_time

                should_alert = self._should_alert_weight_failure(
                    failure_type,
                    self._weight_consecutive_failures,
                    time_since_success,
                    time_since_last_alert
                )

                if should_alert:
                    self._weight_last_alert_time = time.time()

                return should_alert, failure_type
        return False, "unknown"

    def send_weight_failure_alert(self, error_msg: str, failure_type: str, netuid: int = None):
        """Send contextual Slack alert for weight setting failure"""
        if not self.enabled or self.is_miner:
            return

        consecutive = getattr(self, '_weight_consecutive_failures', 0)
        time_since_success = time.time() - getattr(self, '_weight_last_success_time', time.time())
        hours_since_success = time_since_success / 3600

        # Build alert message based on failure type
        if "maximum recursion depth exceeded" in error_msg.lower():
            message = (f"🚨 CRITICAL: Weight setting recursion error\n"
                      f"Hotkey: ...{self.hotkey[-8:]}\n"
                      f"Netuid: {netuid}\n"
                      f"Error: {error_msg}\n"
                      f"This indicates a serious code issue that needs immediate attention.")

        elif "invalid transaction" in error_msg.lower():
            message = (f"🚨 CRITICAL: Subtensor rejected weight transaction\n"
                      f"Hotkey: ...{self.hotkey[-8:]}\n"
                      f"Netuid: {netuid}\n"
                      f"Error: {error_msg}\n"
                      f"This may indicate wallet/balance issues or network problems.")

        elif failure_type == "unknown":
            message = (f"❓ NEW PATTERN: Unknown weight setting failure\n"
                      f"Hotkey: ...{self.hotkey[-8:]}\n"
                      f"Netuid: {netuid}\n"
                      f"Consecutive failures: {consecutive}\n"
                      f"Error: {error_msg}\n"
                      f"This is a new error pattern that needs investigation.")

        else:
            # Prolonged failure alert
            if hours_since_success >= 2:
                urgency = "🚨 URGENT"
                time_msg = f"No successful weight setting in {hours_since_success:.1f} hours"
            else:
                urgency = "⚠️ WARNING"
                time_msg = f"No successful weight setting in {hours_since_success:.1f} hours"

            message = (f"{urgency}: Weight setting issues detected\n"
                      f"Hotkey: ...{self.hotkey[-8:]}\n"
                      f"Netuid: {netuid}\n"
                      f"{time_msg}\n"
                      f"Last error: {error_msg}")

        self.send_message(message, level="error")

    def send_weight_recovery_alert(self, netuid: int = None):
        """Send recovery alert after critical weight setting failures"""
        if not self.enabled or self.is_miner:
            return

        message = (f"✅ Weight setting recovered after failures\n"
                  f"Hotkey: ...{self.hotkey[-8:]}\n"
                  f"Netuid: {netuid}")

        self.send_message(message, level="info")

    def record_no_permit_event(self):
        """Record a no permit event for validators"""
        if not self.is_miner:
            with self.daily_summary_lock:
                self.daily_metrics["no_permit_events"] += 1

    def record_registration_failure(self):
        """Record a registration failure for validators"""
        if not self.is_miner:
            with self.daily_summary_lock:
                self.daily_metrics["registration_failures"] += 1

    def record_burn_uid_change(self, old_uid: int, new_uid: int):
        """Record a change in burn UID for validators"""
        if not self.is_miner:
            with self.daily_summary_lock:
                self.daily_metrics["burn_uid_changes"].append(
                    (old_uid, new_uid, datetime.now(timezone.utc))
                )

    def record_stake_sweep_success(self, amount_tao: float):
        """Record a successful stake sweep for miners"""
        if self.is_miner:
            with self.daily_summary_lock:
                self.daily_metrics["stake_sweeps_count"] += 1
                self.daily_metrics["total_stake_swept"] += amount_tao

    def record_stake_sweep_failure(self):
        """Record a failed stake sweep for miners"""
        if self.is_miner:
            with self.daily_summary_lock:
                self.daily_metrics["stake_sweeps_failed"] += 1

    def record_stake_transfer_success(self, amount_tao: float):
        """Record a successful stake transfer for miners"""
        if self.is_miner:
            with self.daily_summary_lock:
                self.daily_metrics["stake_transfers_count"] += 1
                self.daily_metrics["total_stake_transferred"] += amount_tao

    def record_stake_transfer_failure(self):
        """Record a failed stake transfer for miners"""
        if self.is_miner:
            with self.daily_summary_lock:
                self.daily_metrics["stake_transfers_failed"] += 1

    def shutdown(self):
        """Clean shutdown - save metrics"""
        try:
            self._save_lifetime_metrics()
        except Exception as e:
            bt.logging.error(f"Error during shutdown: {e}")
