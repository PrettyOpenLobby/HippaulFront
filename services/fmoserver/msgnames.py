"""MSG_NAMES: the message id to name table the log lines use."""
from . import (
    charlist, charselect, grouplogin, handshake, inventory, lobapi, move, shop, timesync, trade,
    zoneentry,
)


MSG_NAMES = {handshake.MSG_VERSION: "version", handshake.MSG_CREDENTIALS: "credentials",
             handshake.MSG_HANDSHAKE_OK: "handshake-ok", handshake.MSG_CRED_REPLY: "cred-reply",
             handshake.MSG_SESSION_START: "session-start", handshake.MSG_GAME_HELLO: "game-hello",
             timesync.MSG_TIME_REQ: "time-req", timesync.MSG_TIME_REPLY: "time-reply",
             charlist.MSG_LIST_REQ: "list-req", charlist.MSG_LIST_REPLY: "list-reply",
             handshake.MSG_START_GAME: "start-game", handshake.MSG_START_GAME_OK: "start-game-ok",
             grouplogin.MSG_0154_REQ: "0x154-req", grouplogin.MSG_0154_REPLY: "0x154-reply",
             inventory.MSG_0132_REQ: "0x132-req", inventory.MSG_0132_REPLY: "0x132-reply",
             inventory.MSG_0165_REQ: "0x165-req", inventory.MSG_0165_REPLY: "0x165-reply",
             zoneentry.MSG_0150_REQ: "0x150-req", zoneentry.MSG_0150_REPLY: "0x150-reply",
             charselect.MSG_KEEPALIVE: "keepalive"}
MSG_NAMES[0x013F] = "delete-character"
MSG_NAMES[shop.MSG_SETUP_SAVE] = "setup-save"
MSG_NAMES[shop.MSG_ACQUIRE] = "acquire-part"
MSG_NAMES[shop.MSG_ACQUIRE_REPLY] = "acquire-reply"
MSG_NAMES[move.MSG_MOVE_LIST_REQ] = "move-list-req"
MSG_NAMES[move.MSG_MOVE_LIST_REPLY] = "move-list-reply"
MSG_NAMES[move.MSG_MOVE_REQ] = "move-req"
MSG_NAMES[move.MSG_HANGAR_PW] = "hangar-password"
MSG_NAMES[move.MSG_PLAYTIME_REQ] = "playtime-req"
MSG_NAMES[move.MSG_PLAYTIME_REPLY] = "playtime-reply"
MSG_NAMES[move.MSG_MEMBER_CHECK_REQ] = "member-check-req"
MSG_NAMES[move.MSG_LOGOUT_REQ] = "logout-req"
MSG_NAMES[trade.MSG_TRADE_UPDATE] = "trade-update"
MSG_NAMES[trade.MSG_TRADE_OFFER] = "trade-cancel"
MSG_NAMES[trade.MSG_TRADE_START] = "trade-offer"
MSG_NAMES[trade.MSG_TRADE_ACCEPT] = "trade-accept"
MSG_NAMES[trade.MSG_TRADE_OK] = "trade-ok"
MSG_NAMES[trade.MSG_TRADE_POLL] = "trade-poll"
MSG_NAMES[trade.MSG_TRADE_STATE] = "trade-state"
MSG_NAMES[trade.MSG_TRADE_PUSH] = "trade-push"
MSG_NAMES.update({r: "lobapi-req" for r in lobapi.LOBAPI})
MSG_NAMES.update({v[0]: "lobapi-reply" for v in lobapi.LOBAPI.values()
                  if v[0] not in MSG_NAMES})
