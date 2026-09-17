from app import app, prune_resolved_instructions


def make_instruction(item_id, created_at, status='解除'):
    return {
        'id': item_id,
        'target': '住民',
        'title': f'title-{item_id}',
        'content': f'content-{item_id}',
        'priority': '中',
        'region': '全地域',
        'age_groups': ['20代未満を含む'],
        'status': status,
        'created_at': created_at,
    }


def test_prune_resolved_instructions_keeps_latest_five():
    instructions = [
        make_instruction(1, '2026年09月01日 09:00'),
        make_instruction(2, '2026年09月02日 09:00'),
        make_instruction(3, '2026年09月03日 09:00'),
        make_instruction(4, '2026年09月04日 09:00'),
        make_instruction(5, '2026年09月05日 09:00'),
        make_instruction(6, '2026年09月06日 09:00'),
    ]

    pruned = prune_resolved_instructions(instructions)

    assert len(pruned) == 5
    assert [instruction['id'] for instruction in pruned] == [2, 3, 4, 5, 6]


def test_resolved_instruction_page_renders_list():
    client = app.test_client()
    with client.session_transaction() as session:
        session['logged_in'] = True

    response = client.get('/resolved_instructions')

    assert response.status_code == 200
    assert '解除済み情報一覧' in response.get_data(as_text=True)
