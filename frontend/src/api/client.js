// Shared client for the FundFlow Django API (ARCHITECTURE §5, §7.1).
// Paths are relative to /api, which the Vite dev server proxies to Django.
//
// Tenant selection: every organization-scoped call takes the selected organization slug
// explicitly and sends it as X-Organization. The client keeps no organization state of its own,
// so the caller (App) decides which organization a response belongs to (ARCHITECTURE §7.2).
// An optional AbortSignal is passed straight to fetch; an aborted request rejects with the
// browser's own AbortError, which is never wrapped as an ApiError.

const API_BASE = "/api";

export class ApiError extends Error {
  constructor(message, { status = 0, data = null } = {}) {
    super(message);
    this.name = "ApiError";
    this.status = status; // HTTP status, or 0 when the server could not be reached
    this.data = data; // parsed response body, if any
  }
}

export async function request(path, { org, method = "GET", body, signal } = {}) {
  const headers = { Accept: "application/json" };
  if (org) headers["X-Organization"] = org;

  let payload = body;
  const isFormData = typeof FormData !== "undefined" && body instanceof FormData;
  if (body !== undefined && !isFormData) {
    headers["Content-Type"] = "application/json";
    payload = JSON.stringify(body);
  }
  // For FormData the browser sets multipart/form-data with the correct boundary itself.

  let response;
  try {
    response = await fetch(API_BASE + path, { method, headers, body: payload, signal });
  } catch (error) {
    if (error.name === "AbortError") throw error;
    throw new ApiError("Could not reach the server. Check that the backend is running.");
  }

  const data = await readBody(response);
  if (!response.ok) {
    const detail = data && typeof data === "object" && typeof data.detail === "string" ? data.detail : null;
    throw new ApiError(detail || `Request failed with status ${response.status}.`, {
      status: response.status,
      data,
    });
  }
  return data;
}

async function readBody(response) {
  if (response.status === 204) return null;
  const type = response.headers.get("Content-Type") || "";
  if (type.includes("application/json")) {
    try {
      return await response.json();
    } catch {
      return null;
    }
  }
  return null; // non-JSON bodies (e.g. an HTML error page) carry no usable detail
}

function requireOrg(org) {
  if (!org) throw new ApiError("Select an organization first.");
  return org;
}

// --- Endpoints -------------------------------------------------------------------------------

// No X-Organization header: the organization list is tenant-neutral (D-19).
export const listOrganizations = ({ signal } = {}) => request("/organizations/", { signal });

export const listTransactions = async (org, { signal } = {}) =>
  request("/transactions/", { org: requireOrg(org), signal });

export const listLineItems = async (org, { signal } = {}) =>
  request("/line-items/", { org: requireOrg(org), signal });

export async function importStatement(org, file, { signal } = {}) {
  const form = new FormData();
  form.append("file", file);
  return request("/imports/", { org: requireOrg(org), method: "POST", body: form, signal });
}

export const approveMatch = async (org, matchId) =>
  request(`/matches/${encodeURIComponent(matchId)}/approve/`, { org: requireOrg(org), method: "POST" });

export const rejectMatch = async (org, matchId) =>
  request(`/matches/${encodeURIComponent(matchId)}/reject/`, { org: requireOrg(org), method: "POST" });

export const ignoreTransaction = async (org, transactionId) =>
  request(`/transactions/${encodeURIComponent(transactionId)}/ignore/`, { org: requireOrg(org), method: "POST" });
