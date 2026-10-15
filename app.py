FIXES TO APPLY TO YOUR WORKING app.py

1. In init_db() add imported_files table:

    cur.execute("""
    CREATE TABLE IF NOT EXISTS imported_files(
        filename TEXT PRIMARY KEY
    )
    """)

2. Add after init_db():


def file_imported(filename):

    conn = sqlite3.connect(DB)
    cur = conn.cursor()

    cur.execute(
        "SELECT filename FROM imported_files WHERE filename=?",
        (filename,)
    )

    result = cur.fetchone()

    conn.close()

    return result is not None


def mark_file_imported(filename):

    conn = sqlite3.connect(DB)
    cur = conn.cursor()

    cur.execute(
        "INSERT OR IGNORE INTO imported_files VALUES(?)",
        (filename,)
    )

    conn.commit()
    conn.close()

3. Replace:

except:
    pass

with:

except Exception as e:
    st.error(f"Campey import error: {e}")

in parse_campey()

4. Replace:

except:
    pass

with:

except Exception as e:
    st.error(f"Bulk import error: {e}")

in parse_bulk()

5. In the upload section, change:

for file in files:

To:

for file in files:

    if file_imported(file.name):
        st.sidebar.warning(
            f"{file.name} already imported"
        )
        continue

6. After:

for e in events:
    add_event(e)

Add:

mark_file_imported(file.name)

7. Add a clear calendar button after:

st.sidebar.header("Upload")

Add:

if st.sidebar.button("Clear Calendar"):

    conn = sqlite3.connect(DB)
    cur = conn.cursor()

    cur.execute("DELETE FROM events")
    cur.execute("DELETE FROM imported_files")

    conn.commit()
    conn.close()

    st.rerun()

8. Ensure this line exists in parse_bulk():

if len(txt.strip()) < 10:
    continue

NOT:

if len(txt.strip()) &lt; 10:
