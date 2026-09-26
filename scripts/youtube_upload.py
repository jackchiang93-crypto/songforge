"""Upload one reviewed video to YouTube as private using desktop OAuth."""
from __future__ import annotations

import argparse
from pathlib import Path

SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]


def parse_metadata(path: Path) -> dict:
    text = path.read_text(encoding="utf-8")
    sections = {}
    current = None
    for line in text.splitlines():
        if line in ("TITLE", "DESCRIPTION", "TAGS"):
            current = line
            sections[current] = []
        elif current:
            sections[current].append(line)
    title = "\n".join(sections.get("TITLE", [])).strip()
    description = "\n".join(sections.get("DESCRIPTION", [])).strip()
    tags = [tag.strip() for tag in ",".join(sections.get("TAGS", [])).split(",") if tag.strip()]
    if not title or not description:
        raise ValueError("Metadata needs TITLE and DESCRIPTION sections")
    if len(title) > 100:
        raise ValueError("YouTube title exceeds 100 characters")
    return {"title": title, "description": description, "tags": tags}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("video", type=Path, help="Rendered MP4 file")
    parser.add_argument("metadata", type=Path, help="Metadata.txt exported from SONGFORGE")
    parser.add_argument("--client-secrets", type=Path, required=True, help="Google Desktop OAuth client JSON")
    parser.add_argument("--token", type=Path, default=Path(".youtube-token.json"))
    parser.add_argument("--upload", action="store_true", help="Actually upload; default is a local preview")
    args = parser.parse_args()
    if not args.video.is_file() or not args.metadata.is_file():
        parser.error("Video and metadata files must exist")
    info = parse_metadata(args.metadata)
    print(f"Video: {args.video.resolve()}\nTitle: {info['title']}\nTags: {', '.join(info['tags'])}\nPrivacy: private")
    if not args.upload:
        print("Preview only. Add --upload after checking the video and metadata.")
        return
    if not args.client_secrets.is_file():
        parser.error("OAuth client secrets JSON not found")

    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow
    from googleapiclient.discovery import build
    from googleapiclient.http import MediaFileUpload

    credentials = None
    if args.token.is_file():
        credentials = Credentials.from_authorized_user_file(str(args.token), SCOPES)
    if not credentials or not credentials.valid:
        if credentials and credentials.expired and credentials.refresh_token:
            credentials.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(str(args.client_secrets), SCOPES)
            credentials = flow.run_local_server(port=0)
        args.token.write_text(credentials.to_json(), encoding="utf-8")
        args.token.chmod(0o600)

    youtube = build("youtube", "v3", credentials=credentials)
    request = youtube.videos().insert(
        part="snippet,status",
        body={
            "snippet": {"title": info["title"], "description": info["description"],
                        "tags": info["tags"], "categoryId": "10"},
            "status": {"privacyStatus": "private", "selfDeclaredMadeForKids": False},
        },
        media_body=MediaFileUpload(str(args.video), mimetype="video/mp4", chunksize=8 * 1024 * 1024, resumable=True),
    )
    response = None
    while response is None:
        progress, response = request.next_chunk()
        if progress:
            print(f"Uploaded {progress.progress():.0%}")
    if not response.get("id"):
        raise RuntimeError(f"Upload returned no video ID: {response}")
    print(f"Private video: https://www.youtube.com/watch?v={response['id']}")


if __name__ == "__main__":
    main()
