"""Gemini explanations of the charts and tables the app shows (`POST /api/v1/explain`).

`context` turns what the viewer sees (a Plotly figure or a table, its title, caption and the page text) into compact
text and checks which numbers in the answer are not in that text; `gemini` calls the model.
"""
