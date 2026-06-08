import argparse
from html.parser import HTMLParser
import re
import sys
from urllib.error import HTTPError, URLError
from urllib.request import urlopen


def extract_doc_id(doc_url: str) -> str:
	"""Extract Google Doc ID from a standard Google Docs URL."""
	match = re.search(r"/document/d/([a-zA-Z0-9_-]+)", doc_url)
	if not match:
		raise ValueError(
			"Invalid Google Docs URL. Expected format like: "
			"https://docs.google.com/document/d/<DOC_ID>/edit"
		)
	return match.group(1)


def fetch_google_doc_text(doc_url: str) -> str:
	"""Fetch plain text from a Google Doc (regular or published URL)."""
	pub_match = re.search(r"/document/d/e/([a-zA-Z0-9_-]+)/pub", doc_url)
	if pub_match:
		separator = "&" if "?" in doc_url else "?"
		export_url = f"{doc_url}{separator}output=txt"
	else:
		doc_id = extract_doc_id(doc_url)
		export_url = f"https://docs.google.com/document/d/{doc_id}/export?format=txt"

	try:
		with urlopen(export_url) as response:
			return response.read().decode("utf-8")
	except HTTPError as exc:
		if exc.code in (401, 403):
			raise PermissionError(
				"Cannot access the document. Make sure it is shared publicly (Anyone with the link)."
			) from exc
		raise RuntimeError(f"HTTP error while fetching doc: {exc.code}") from exc
	except URLError as exc:
		raise ConnectionError(f"Network error while fetching doc: {exc}") from exc


def parse_grid_entries(text: str) -> list[tuple[int, int, str]]:
	"""Parse (x, y, char) entries from Google Doc text."""
	entries: list[tuple[int, int, str]] = []

	patterns = [
		re.compile(r"^\s*(\d+)\s*[,\t ]+\s*(\S+)\s*[,\t ]+\s*(\d+)\s*$"),  # x, C, y
		re.compile(r"^\s*(\d+)\s*[,\t ]+\s*(\d+)\s*[,\t ]+\s*(\S+)\s*$"),  # x, y, C
		re.compile(
			r"x\s*[:=]\s*(\d+).*?y\s*[:=]\s*(\d+).*?(?:char|character)\s*[:=]\s*(\S+)",
			re.IGNORECASE,
		),
	]

	for raw_line in text.splitlines():
		line = raw_line.strip()
		if not line:
			continue

		matched = False
		for pattern in patterns:
			match = pattern.search(line)
			if not match:
				continue

			g1, g2, g3 = match.groups()
			if g2.isdigit():
				x, y, char = int(g1), int(g2), g3
			else:
				x, y, char = int(g1), int(g3), g2
			char = char.strip()
			if not char:
				break
			entries.append((x, y, char))
			matched = True
			break

		if matched:
			continue

		tokens = re.findall(r"\d+|[^\s\d]", line)
		ints = [int(token) for token in tokens if token.isdigit()]
		chars = [token for token in tokens if not token.isdigit()]
		if len(ints) >= 2 and len(chars) >= 1:
			x, y = ints[0], ints[1]
			entries.append((x, y, chars[0]))

	entries = [
		(x, y, char)
		for x, y, char in entries
		if 0 <= x <= 10000 and 0 <= y <= 10000 and char.strip()
	]

	if not entries:
		raise ValueError(
			"No valid grid data found. Expected lines like 'x, A, y' or 'x y A'."
		)

	return entries


class GoogleDocTableParser(HTMLParser):
	"""Extract table rows/cells from published Google Docs HTML."""

	def __init__(self) -> None:
		super().__init__()
		self.rows: list[list[str]] = []
		self._in_tr = False
		self._in_td = False
		self._current_row: list[str] = []
		self._cell_chunks: list[str] = []

	def handle_starttag(self, tag: str, attrs) -> None:
		if tag == "tr":
			self._in_tr = True
			self._current_row = []
		elif tag == "td" and self._in_tr:
			self._in_td = True
			self._cell_chunks = []

	def handle_data(self, data: str) -> None:
		if self._in_td:
			self._cell_chunks.append(data)

	def handle_endtag(self, tag: str) -> None:
		if tag == "td" and self._in_td:
			cell_text = "".join(self._cell_chunks).strip()
			self._current_row.append(cell_text)
			self._in_td = False
			self._cell_chunks = []
		elif tag == "tr" and self._in_tr:
			if self._current_row:
				self.rows.append(self._current_row)
			self._in_tr = False
			self._current_row = []


def parse_grid_entries_from_published_doc_html(html: str) -> list[tuple[int, int, str]]:
	"""Parse (x, y, char) entries from a published Google Doc HTML table."""
	parser = GoogleDocTableParser()
	parser.feed(html)

	entries: list[tuple[int, int, str]] = []
	for row in parser.rows:
		if len(row) < 3:
			continue
		x_raw, char_raw, y_raw = row[0].strip(), row[1].strip(), row[2].strip()
		if not (x_raw.isdigit() and y_raw.isdigit() and char_raw):
			continue
		x = int(x_raw)
		y = int(y_raw)
		if not (0 <= x <= 10000 and 0 <= y <= 10000):
			continue
		entries.append((x, y, char_raw[0]))

	if not entries:
		raise ValueError("No valid grid rows found in published Google Doc table.")

	return entries


def print_google_doc_character_grid(doc_url: str) -> None:
	"""Retrieve, parse, and print the character grid encoded in a Google Doc."""
	if re.search(r"/document/d/e/[a-zA-Z0-9_-]+/pub", doc_url):
		with urlopen(doc_url) as response:
			html = response.read().decode("utf-8")
		entries = parse_grid_entries_from_published_doc_html(html)
	else:
		text = fetch_google_doc_text(doc_url)
		entries = parse_grid_entries(text)

	min_x = min(x for x, _, _ in entries)
	min_y = min(y for _, y, _ in entries)
	max_x = max(x for x, _, _ in entries)
	max_y = max(y for _, y, _ in entries)

	width = max_x - min_x + 1
	height = max_y - min_y + 1

	grid = [[" " for _ in range(width)] for _ in range(height)]
	for x, y, char in entries:
		grid[y - min_y][x - min_x] = char

	for row in grid:
		print("".join(row))


def main() -> None:
	parser = argparse.ArgumentParser(
		description="Decode and print a character grid from a Google Doc URL."
	)
	parser.add_argument("doc_url", help="Google Doc URL")
	args = parser.parse_args()

	try:
		print_google_doc_character_grid(args.doc_url)
	except Exception as exc:
		print(f"Error: {exc}", file=sys.stderr)
		sys.exit(1)


if __name__ == "__main__":
	main()
