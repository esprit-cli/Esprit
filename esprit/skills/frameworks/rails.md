---
name: rails
description: Security testing playbook for Ruby on Rails applications covering mass assignment, deserialization, and ActiveRecord injection
---

# Ruby on Rails

Security testing for Ruby on Rails applications. Focus on mass assignment via strong parameters bypass, ActiveRecord injection, unsafe deserialization of Marshal/YAML, send_file path traversal, and Action Cable authorization gaps.

## Attack Surface

**Core Components**
- Routing: `routes.rb`, RESTful resources, nested routes, constraints, engines, namespace/scope
- Controllers: `ApplicationController`, before/after actions, `skip_before_action`, `protect_from_forgery`
- Views: ERB, HAML, Slim templates, partials, layouts, helpers

**Data Handling**
- ActiveRecord: query interface, scopes, `find_by_sql`, `where`, `order`, `pluck`, Arel
- Strong parameters: `params.require().permit()`, `permit!`, nested attributes
- ActiveStorage: file uploads, direct uploads, variants, service URLs

**Authentication & Authorization**
- Devise: strategies, OmniAuth integration, rememberable, lockable, confirmable
- Pundit/CanCanCan: policy objects, abilities, authorization checks
- `has_secure_password`, `authenticate` method

**Real-Time**
- Action Cable: channels, streams, subscriptions, connection authentication
- Turbo Streams: server-sent updates, WebSocket broadcasts

**Deployment**
- Puma/Unicorn, nginx/Apache reverse proxy, Heroku, Docker, Kamal

## High-Value Targets

- Admin interfaces: ActiveAdmin, RailsAdmin, Administrate, custom `/admin` namespace
- Devise endpoints (`/users/sign_in`, `/users/password/new`, `/users/confirmation`)
- ActiveStorage URLs (`/rails/active_storage/blobs/`, `/rails/active_storage/disk/`)
- Action Cable mount point (`/cable`), Action Mailbox ingress (`/rails/action_mailbox/`)
- Sidekiq Web UI (`/sidekiq`) and delayed job dashboards
- Mounted engines and rack apps without authentication

## Reconnaissance

**Framework Fingerprinting**
```
X-Runtime: 0.123456 (Rails timing header)
X-Request-Id: uuid (Rails request ID)
Set-Cookie: _app_session= (Rails session cookie)
```

**Route Enumeration**
```
GET /rails/info/routes   # Full route listing (dev mode)
GET /rails/info/properties  # Rails version, environment, middleware
GET /nonexistent  # Routing error page lists all routes in dev
```

**Secret and Config Leakage**
```
GET /config/database.yml    # Via path traversal
GET /config/secrets.yml
GET /config/master.key      # If exposed, decrypts all credentials
GET /config/credentials.yml.enc
```

## Key Vulnerabilities

### Mass Assignment

**Strong Parameters Bypass**
```ruby
# Vulnerable: permit! allows ALL parameters
params.require(:user).permit!

# Vulnerable: overly permissive permit
params.require(:user).permit(:name, :email, :role, :admin)

# Vulnerable: nested attributes without restriction
params.require(:order).permit(:status, line_items_attributes: {})
```

**Exploitation**
```
POST /users {"user": {"name": "test", "role": "admin", "admin": true}}
POST /orders {"order": {"line_items_attributes": [{"id": 1, "price": "0.01"}]}}
```

- Try `_attributes` suffix for nested associations, `_ids` suffix for `has_many`: `{"user": {"role_ids": [1,2,3]}}`
- JSON API format differences: `data[attributes][role]` vs `user[role]`

### ActiveRecord Injection

**Unsafe Query Patterns**
```ruby
User.where("name = '#{params[:name]}'")       # String interpolation
User.order(params[:sort])                       # Order clause injection
User.pluck(params[:field])                      # Arbitrary column extraction
User.find_by_sql("SELECT * FROM users WHERE id = #{params[:id]}")
```

**Injection Payloads**
```
sort=CASE WHEN (SELECT 1)=1 THEN name ELSE email END
name=admin' AND pg_sleep(5)--
name=' UNION SELECT username,password,3,4 FROM users--
```

### Deserialization Attacks

**Marshal Deserialization**
```ruby
Marshal.load(Base64.decode64(params[:data]))  # Enables RCE via gadget chains
```

**YAML Deserialization**
```ruby
YAML.load(params[:config])  # Unsafe before Ruby 3.1; use YAML.safe_load
```
YAML RCE payloads leverage `!ruby/object:` tags to instantiate arbitrary classes and chain to code execution via Gem::Requirement/Gem::Installer gadgets.

**Cookie-Based Attacks**
- Rails `secret_key_base` compromise enables session cookie forgery and deserialization RCE
- Check: `config/secrets.yml`, `config/credentials.yml.enc`, `ENV['SECRET_KEY_BASE']`, git history

### send_file Path Traversal

```ruby
send_file params[:path]                                    # Direct path control
send_file "#{Rails.root}/uploads/#{params[:filename]}"     # Concatenation
send_data File.read("uploads/#{params[:name]}")            # File read
```
```
GET /download?path=../../config/database.yml
GET /download?filename=..%2f..%2fconfig%2fdatabase.yml
```

### Action Cable Authorization

```ruby
# Vulnerable: channel without authorization
class AdminChannel < ApplicationCable::Channel
  def subscribed
    stream_from "admin_notifications"  # No user.admin? check
  end
end
```

- Subscribe to other users' channels via user_id parameter in subscription identifier
- Missing `identified_by` and auth logic in `ApplicationCable::Connection`

### CSRF Bypass

- `skip_before_action :verify_authenticity_token` on API endpoints
- `protect_from_forgery with: :null_session` — fails silently, request proceeds without session
- Wildcard CORS + credentials enabling cross-origin state changes

### Cross-Site Scripting

```erb
<%= raw user_input %>
<%= user_input.html_safe %>
<%== user_input %>
<%= link_to "Click", params[:url] %>  <%# javascript: URLs %>
```

## Bypass Techniques

- Parameter key format switching: `user[role]` vs `user.role` vs JSON `{"user":{"role":"admin"}}`
- HTTP method override via `_method` parameter or `X-HTTP-Method-Override` header
- `.json`/`.xml`/`.csv` format suffix triggering different renderers with varying escaping
- `Accept` header manipulation forcing different response formats
- Race conditions in `before_action` chains with database-level checks

## Testing Methodology

1. **Enumerate routes** - Extract routes from dev error pages, route listing, or asset files
2. **Mass assignment audit** - Test all create/update endpoints with extra fields (role, admin, permissions)
3. **SQL injection** - Identify `where`, `order`, `group`, `pluck` with user input; test string interpolation
4. **Deserialization** - Check for Marshal.load, YAML.load, cookie manipulation with known secret_key_base
5. **File access** - Test send_file/send_data endpoints with traversal payloads
6. **Action Cable** - Connect to WebSocket, enumerate channels, test subscription authorization
7. **Auth bypass** - Test `skip_before_action`, format-specific auth gaps, CSRF bypass vectors
8. **Secret exposure** - Check for `master.key`, `secrets.yml`, `SECRET_KEY_BASE` in env/git

## Validation Requirements

- Mass assignment proof showing modification of privileged fields (role escalation, price manipulation)
- SQL injection with data extraction or authentication bypass evidence
- Deserialization RCE with command output (use safe commands like `whoami` or `hostname`)
- Path traversal reading files outside intended directory (config files, source code)
- Action Cable unauthorized subscription to privileged channels with received data
- CSRF bypass achieving state change via cross-origin request

## False Positives

- Strong parameters properly configured with `permit()` whitelist (not `permit!`)
- `YAML.safe_load` used instead of `YAML.load` (safe against deserialization)
- `send_file` with `:root` option or `ActiveStorage::Blob` serving (path-constrained)
- CSRF token absence on truly stateless API endpoints using token authentication only

## Impact

- Mass assignment: privilege escalation, data manipulation, business logic bypass
- ActiveRecord injection: full database compromise, credential theft, data exfiltration
- Deserialization: remote code execution, full server compromise
- Path traversal: source code disclosure, credential file access, database config exposure
- Action Cable exploitation: unauthorized real-time data access, message injection
- secret_key_base compromise: session forging, RCE via cookie deserialization

## Pro Tips

- Rails `secret_key_base` is equivalent to full compromise: it enables cookie forgery, deserialization RCE, and message verifier bypass. Always hunt for it in git history, env files, CI configs, and error pages.
- `params.permit!` is the Rails equivalent of disabling security. Grep the entire codebase for it.
- Rails `render` with user input can lead to file read: `render file: params[:template]` reads arbitrary files.
- `ActiveRecord::Base.connection.execute()` bypasses all ORM protections. Search for direct SQL execution.
- Check for `config.force_ssl = false` in production — session cookies may lack Secure flag.
- Rails engines (Sidekiq::Web, ActiveAdmin) mounted without authentication middleware are common backdoors.

## Summary

Rails security testing prioritizes mass assignment via strong parameters bypass (`permit!`, nested attributes, `_ids` suffix), ActiveRecord injection through string interpolation in queries, and deserialization attacks via Marshal/YAML with compromised `secret_key_base`. Path traversal through `send_file`, Action Cable authorization gaps, and CSRF bypass via `skip_before_action` are high-value targets. The `secret_key_base` is the single most critical secret -- its compromise enables session forging and RCE. Always check mounted engines for missing authentication and test format-specific rendering paths.
