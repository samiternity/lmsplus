![](app/static/logo-dark-expanded.png)
---
[![Join LMS+ Discord](https://img.shields.io/badge/Join%20Discord-LMS+-5865F2?style=for-the-badge&logo=discord&logoColor=white)](https://discord.com/invite/DVUq7gaM3E)

**[LMS+](https://lmsplus.xyz)** is a web wrapper for Bahria University's CMS and LMS. It pulls your key information into a cleaner interface and adds assignment reminders and AI integration via an MCP server.

Your credentials are encrypted and stored securely. Nobody, including me, can view your CMS password. Hundreds of students across Bahria's Karachi, Lahore, and Islamabad campuses use it.

### Features

---
- CMS survey auto fill  
- Assignment and Attendance notifications
- Download all lectures in 1 click
- No logouts
- CMS passwords stored securely
- MCP server (AI connector/plugin)
- Modern interface + Dark mode
- Shows how many classes you can safely skip (Attendance)
- Pending & overdue assignments on one page.



### Connect an MCP app

Add `https://lmsplus.xyz/mcp` as a remote MCP server in your AI app. If the app
supports automatic OAuth registration, sign in to LMS+ and approve read-only
assignment access when prompted.

If the app asks for manual OAuth credentials, copy its OAuth redirect URI and
register it with LMS+. This example uses `client_secret_post`. Use
`client_secret_basic` if your app requires HTTP Basic client authentication.

```sh
curl --fail-with-body https://lmsplus.xyz/register \
  --header 'Content-Type: application/json' \
  --data '{
    "redirect_uris": ["https://your-app.example/oauth/callback"],
    "token_endpoint_auth_method": "client_secret_post",
    "grant_types": ["authorization_code", "refresh_token"],
    "response_types": ["code"],
    "scope": "assignments:read"
  }'
```

Enter the returned `client_id` and `client_secret` in the app. Keep the secret
private. The app must support OAuth authorization code with S256 PKCE.

This is an independent project and is not affiliated with Bahria University. I built it because I was tired of constantly getting logged out of CMS, missing assignment deadlines, and checking my attendance every day for mistaken absent marks.

Want to contribute or self-host? Check the [contribution guide](CONTRIBUTING.md) for setup instructions. Please contact me before starting work on a new feature.

Licensed under the [MIT License](LICENSE).