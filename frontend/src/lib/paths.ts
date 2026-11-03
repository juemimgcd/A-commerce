// Next Link/router add basePath themselves; native requests and forms do not.
export const basePath = process.env.NEXT_PUBLIC_COMMERCE_BASE_PATH ?? "";

export function webPath(path: string) {
  return `${basePath}${path}`;
}
