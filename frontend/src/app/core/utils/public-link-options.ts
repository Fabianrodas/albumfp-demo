export function publicLinkOptions(password: string, allowOriginalDownload: boolean, showMetadata: boolean) {
  return {
    password: password.trim() || null,
    allow_original_download: allowOriginalDownload,
    show_metadata: showMetadata,
  };
}
