# Web-client gateway commands used by the group features

What the relay's own session can do through the Feishu web client's gateway, as tried on 2026-10-13 with the
relay account in a group made for the purpose (中继自测群). These are the web client's internal interfaces, not
published ones: a field or a behaviour not written here has not been observed. Do not guess it; report it.

Raw outputs: `docs/work/probes/2026-10-13-relay-auto-p0.out.txt`, `…-relay-auto-p0-c.out.txt`; full field lists
of every message below: `docs/work/probes/2026-10-13-web-gateway-messages.out.txt`.

## Calling a command

`bridge.client.api("<package>.<Name>Request", {...})` (see `RecoveringLarkClient` in `bridge.py`; it needs larkx,
so import nothing from larkx at module top level).

- The request is a dict in the message's own field names (camelCase). Enum fields take the enum name as a
  string (`"GROUP"`, `"ONLY_ADMIN"`, `"JOIN_CHAT"`, `"APP"`). An int64 field accepts an int or a numeric string.
- When the response has a payload, the call returns it as a dict; enum fields come back as numbers.
- A command that succeeds with nothing to say returns the envelope instead of a payload:
  `{'payloadType': 1, 'cmd': 1, 'cid': '<id>'}`.
- A refused command is answered with **HTTP 400** and a failure envelope in the body: `payloadType` is not 1 and
  the reason is the text in `sid`, for example
  `{'sid': 'you are already in target chat:7696053062053530578', 'payloadType': 4012, 'cmd': 3, 'cid': '…'}`.
  The client's `_gateway_post` calls `raise_for_status()`, so `bridge.client.api(...)` raises `requests.HTTPError`
  and the envelope is never returned. (The first version of this file said the envelope comes back as the return
  value: that was true only of the probe, which posted without `raise_for_status()`. Seen on the live system on
  2026-10-13.) Feature handlers therefore take the client from `group_admin.require_client(bridge)`: its
  `.api(...)` turns that error into a `GroupAdminError` carrying Feishu's reason.
- A chat object in a response has, among others: `id`, `name`, `type` (2 = group), `ownerId`, `isCrossTenant`,
  `userCount` (people), `memberCount` (people and bots), `postType`, `lastMessagePosition`, `isDissolved`.
  `bridge.client.chat_infos([chat_id])` returns the same object by chat id.

## Commands

| What | Request (cmd) | Request fields used | Observed result |
|---|---|---|---|
| Create a group | `chats.PutChatRequest` (13) | `type: "GROUP"`, `groupName`, `groupDesc`, `isCrossTenant: true`, `cid: <uuid hex>` | `{chat, feedCard}`; works with the account as the only member; the account is the owner; `postType` 1 |
| Who may post | `chats.PatchChatPostUserRequest` (254) | `chatId`, `postType: "ONLY_ADMIN"` or `"ANYONE"` | empty body. A read right after still shows the old `postType`; two minutes later it shows 2. `ChatPostType`: ANYONE 1, ONLY_ADMIN 2, WHITE_LIST 3. A custom bot's webhook still posts under ONLY_ADMIN |
| Invite link | `chats.PullChatLinkTokenRequest` (5055) | `chatId`, `expiredDay: 7` | `{token (36 chars), sharedUrl, kouLing (text to paste), expireTime (epoch seconds), isUnlimited}`; `sharedUrl` is `https://applink.feishu.cn/client/chat/chatter/add_by_link?link_token=<token>` and `link_token` is its only query parameter |
| What a link leads to | `chats.PullChatLinkInfoRequest` (5057) | `token` | `{alreadyInChat, chat, expireTime}` when the account is already a member; `ownerChatter` / `inviterChatter` were absent in that case and are not observed otherwise |
| Join by link | `chats.PutChatterViaLinkRequest` (5056) | `token` | already a member: failure envelope, `payloadType` 4012, `sid` "you are already in target chat:<chat id>". Declared response `{chat}`; a successful join is not observed |
| Members | `chats.PullChatChattersV2Request` (5950) | `chatId`, `limit`, `cursor` | `{chatterJoinTimeSec: {<user id>: <epoch s>}, chatterMetas, chatterWeights, nextCursor, …}`; the keys of `chatterJoinTimeSec` are the member ids, the account's own id included |
| Join and leave log | `chats.PullChatJoinLeaveHistoryRequest` (5029) | `chatId`, `count`, `cursor` | `{chatterHistory: [{id, action (JOIN 1, LEAVE 2), event, chatterId, chatId, createTime, chatterType (USER 1, BOT 2)}], hasMore, nextCursor}`; 20 rows a page |
| Join requests | `chats.PullChatApplicationsRequest` (5002) | `type: "JOIN_CHAT"`, `count`, `isDescOrder: true`, `chatId` | success with an empty body when there is none. Declared row: `{id, type, chatId, contactSummary, applyTime, processedTime, status (PENDING 1, AGREED 2, REFUSED 3, DELETED 4, EXPIRED 5), extraMessage}`; a row is not observed |
| Decide a join request | `chats.ProcessChatApplicationRequest` (5004) | `id`, `status: "AGREED"` or `"REFUSED"` | not tried |
| Add a member | `chats.PutChatChatterRequest` (47) | `chatId`, `chatterIds: [<user id>]` | `{chat, chatChatterVersion}`; the member is in the list at once |
| Remove a member | `chats.DeleteChatChatterRequest` (45) | `chatId`, `chatterIds: [<user id>]` | `{chat, chatChatterVersion}`; the member is gone at once |
| Read messages | `messages.PullMessagesByPositionsRequest` | `chatId`, `positions: [int]` | `{messages: {<key>: {id, type, fromId, position, content (bytes), status, …}}}`; `bridge.client.pull_history` already decodes it |
| Owner recalls a message | `messages.DeleteGroupMessageRequest` (78) | `id: <message id>` | works on a message a custom bot posted. Read back: `status` 2, empty `content`, `removerId` = the account |
| Mark a message urgent | `urgent.PutUrgentRequest` (30000) | `messageId` (a message the account sent), `chatterIds: [<user id>]`, `urgentType: "APP"`, `chatId` | `{urgent: {messageId, type: 1, sendTime, fromId}, chatterIds, urgentAckIds: {<user id>: <ack id>}}` |
| Urgent receipts | `urgent.PullUrgentsAckStatusRequest` (30005) | `messageIds: [id]` | `{urgentAcks: {<message id>: {urgentAckList: [{ackId, messageId, toId, urgentId, sendTime, type, status, phoneStatus}]}}}` |

## Sending a text as the account, and finding its id

`larkx.proto.builders.build_send_message_packet(text, chat_id, request_id)` builds the packet (command 5) and
`bridge.client._gateway_post(packet)` sends it; `larkx.proto.ids.generate_long_request_id()` gives the request id.
The response was not decoded into a message id. The id was found by reading the chat's last two positions
(`lastMessagePosition` from the chat object) and taking the newest message whose `fromId` is the account.

## A custom bot's message as the account sees it

In a group the account is in, a message posted through a custom bot's webhook arrives over the WebSocket like
any other message (the bridge counted it as ignored for a chat it does not listen to). Read back from history
it has `type` TEXT, `fromId` equal to the bot's `bot_id` (the id `bridge.client.chat_bots` lists), and its text.
The text of a relayed post or card as read back has not been compared with what was sent; see probe P0-8 in
the plan before relying on it.

## The member list contains bots

`chats.PullChatChattersV2Request` lists a group's bots among its members (seen 2026-10-13: the account and the
relay's custom bot), with nothing in the answer to tell them apart. `bridge.client.chat_bots(chat_id)` lists the
bots (`botCollection[].bots`, custom and app bots alike); the members handler marks each member with `is_bot`
and `is_self`.

## Mentioning a member through a custom bot's webhook

A custom bot's webhook mentions a member by the numeric id the member list gives (the keys of
`chatterJoinTimeSec`). Both forms were accepted and read back with the member in the message's `atUserIdList`:
text `{"msg_type": "text", "content": {"text": "… <at user_id=\"<id>\">name</at> …"}}`, and a post line
containing `{"tag": "at", "user_id": "<id>"}`. The server replaces the name written inside `<at>` with the
member's display name. Tried with a member of the account's own tenant only (the account itself).

## Not observed

A successful join by link; a join request row and deciding one; `ownerChatter` / `inviterChatter` of a link;
SMS or phone urgent; how a relayed post or card reads back; a webhook mention of a member from another tenant; how many members one message can mention; any of these commands in a group the account
does not own.
