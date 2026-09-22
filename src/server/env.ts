/**
 * A value that is still the `.env.local.template` placeholder, e.g.
 * `<Test Anthropic API Key>` or `<Sentry DSN>`. These are intentionally
 * non-empty so the app boots without every secret, but passing one to a
 * provider produces a confusing "invalid x-api-key"-style error. Treat them
 * as unset so `requiredEnv` fails fast with a clear message instead.
 */
function isPlaceholder(value: string): boolean {
  const trimmed = value.trim();
  return trimmed.startsWith('<') && trimmed.endsWith('>');
}

export function env(name: string): string {
  const value = process.env[name] ?? '';
  return isPlaceholder(value) ? '' : value;
}

export function requiredEnv(name: string): string {
  const value = env(name);
  if (!value) throw new Error(`${name} is not set`);
  return value;
}

export function webhookBaseUrl(requestUrl: string): string {
  const configuredUrl = env('WEBHOOK_BASE_URL');
  if (configuredUrl) return configuredUrl.replace(/\/$/, '');
  return new URL(requestUrl).origin;
}
