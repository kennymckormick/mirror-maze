from mirror_maze import MirrorMaze

def test_fast_host_pose_matches_full_export_without_mutation():
    with MirrorMaze(topology='hex',seed='HOST-STATE-CONTRACT')as env:
        for actions in [['left'],['forward','right','forward'],['left','right']*50]:
            env.perform(actions,stop_on_collision=False)
            expected=env.export_episode()['state']
            assert env.review_state()==expected
            assert env.review_state()==expected
        assert 'cell'not in env.state()and'heading'not in env.state()
