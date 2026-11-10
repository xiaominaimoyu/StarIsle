import { handleVisitorPull, jsonResponse } from "../../_lib/visitor-messages.js";

export async function onRequestPost(context) {
  return handleVisitorPull(context.request, context.env);
}

export async function onRequest() {
  return jsonResponse(405, "METHOD_NOT_ALLOWED");
}
