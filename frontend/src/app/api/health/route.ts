export function GET() {
  return Response.json(
    { status: "ready", service: "commerce-web" },
    { headers: { "Cache-Control": "no-store" } },
  );
}
